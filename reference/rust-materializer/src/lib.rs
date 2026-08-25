//! Transport-injected Tokio reference materializer for nested Asset v2.
use async_trait::async_trait;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{
    fmt,
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWriteExt};
use url::Url;
pub const CLIENT_CAPABILITY: u32 = 2;
pub const MAX_PARTS: usize = 4096;
pub const MAX_ASSET_BYTES: u64 = 8 * 1024 * 1024 * 1024 * 1024;
pub const MAX_URL_BYTES: usize = 8192;
pub const MAX_REDIRECTS: usize = 5;
/// Checked cross-language scenario vocabulary; Rust tests and Python tests both
/// load this exact source so additions cannot silently drift.
pub const FIXTURE_VECTORS: &str = include_str!("../../materializer-fixtures.json");
#[derive(Clone, Debug, Deserialize)]
pub struct Asset {
    pub size_bytes: u64,
    pub sha256: String,
    pub urls: Vec<String>,
    pub parts: Vec<Part>,
}
#[derive(Clone, Debug, Deserialize)]
pub struct Part {
    pub number: u32,
    pub size_bytes: u64,
    pub sha256: String,
    pub urls: Vec<String>,
}
#[derive(Clone, Debug)]
pub enum TransportError {
    Unavailable(String),
    Integrity(String),
}
pub type Body = Box<dyn AsyncRead + Send + Unpin>;
#[async_trait]
pub trait Transport: Send + Sync {
    /// Return a streaming body for a GET after at most five same-origin HTTPS
    /// redirects.  Implementations must reject HTTPS-origin changes/downgrades;
    /// this is part of the injected transport contract, not an ambient client
    /// setting.
    async fn get(&self, url: Url) -> std::result::Result<Body, TransportError>;
}
#[derive(Debug)]
pub enum Error {
    Invalid(String),
    Unavailable,
    Integrity(String),
    Io(std::io::Error),
}
impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Invalid(x) | Self::Integrity(x) => f.write_str(x),
            Self::Unavailable => f.write_str("all mirrors unavailable"),
            Self::Io(e) => e.fmt(f),
        }
    }
}
impl std::error::Error for Error {}
impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Self::Io(e)
    }
}
type R<T> = std::result::Result<T, Error>;
fn hash_ok(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|x| x.is_ascii_digit() || (b'a'..=b'f').contains(&x))
}
fn url(raw: &str, base: Option<&Url>) -> R<Url> {
    if raw.is_empty() || raw.len() > MAX_URL_BYTES || raw.bytes().any(|x| x < 32 || x == 127) {
        return Err(Error::Invalid("invalid URL".into()));
    }
    let mut decoded = raw.to_owned();
    for _ in 0..8 {
        let next = percent_encoding::percent_decode_str(&decoded)
            .decode_utf8_lossy()
            .into_owned();
        if next == decoded {
            break;
        }
        decoded = next;
    }
    let parsed_raw = Url::parse(raw);
    let relative = parsed_raw.is_err();
    if decoded.contains('\\')
        || decoded
            .split('?')
            .next()
            .unwrap_or("")
            .split('/')
            .any(|x| x == "..")
        || decoded.bytes().any(|x| x < 32 || x == 127)
        || (relative && (raw.contains('%') || decoded.contains('?') || decoded.contains('#')))
    {
        return Err(Error::Invalid("unsafe URL".into()));
    }
    let u = match parsed_raw {
        Ok(u) => u,
        Err(_) => {
            if raw.starts_with("//")
                || raw.contains('\\')
                || raw.contains('?')
                || raw.contains('#')
                || raw.split('/').any(|x| x == "..")
            {
                return Err(Error::Invalid("unsafe relative URL".into()));
            }
            base.ok_or_else(|| Error::Invalid("relative URL requires trusted base".into()))?
                .join(raw)
                .map_err(|_| Error::Invalid("invalid relative URL".into()))?
        }
    };
    if u.scheme() != "https"
        || u.host_str().is_none()
        || !u.username().is_empty()
        || u.password().is_some()
        || u.fragment().is_some()
    {
        return Err(Error::Invalid("URL must be credential-free HTTPS".into()));
    }
    Ok(u)
}
fn validate(a: &Asset, trusted: Option<&str>) -> R<Vec<Vec<Url>>> {
    let base = trusted.map(|x| url(x, None)).transpose()?;
    if a.size_bytes == 0
        || a.size_bytes > MAX_ASSET_BYTES
        || !hash_ok(&a.sha256)
        || a.urls.is_empty() == a.parts.is_empty()
    {
        return Err(Error::Invalid(
            "invalid asset transport, size, or hash".into(),
        ));
    }
    let urls = |v: &Vec<String>| -> R<Vec<Url>> {
        if v.is_empty() {
            return Err(Error::Invalid("empty mirrors".into()));
        }
        let mut r = vec![];
        for x in v {
            let u = url(x, base.as_ref())?;
            if r.iter().any(|z| z == &u) {
                return Err(Error::Invalid("duplicate URL".into()));
            }
            r.push(u)
        }
        Ok(r)
    };
    if !a.urls.is_empty() {
        return Ok(vec![urls(&a.urls)?]);
    }
    if a.parts.len() > MAX_PARTS {
        return Err(Error::Invalid("too many parts".into()));
    }
    let mut sum = 0u64;
    let mut r = vec![];
    for (i, p) in a.parts.iter().enumerate() {
        if p.number != (i + 1) as u32
            || p.size_bytes == 0
            || p.size_bytes > MAX_ASSET_BYTES
            || !hash_ok(&p.sha256)
        {
            return Err(Error::Invalid("invalid part".into()));
        }
        sum = sum
            .checked_add(p.size_bytes)
            .filter(|x| *x <= MAX_ASSET_BYTES)
            .ok_or_else(|| Error::Invalid("size overflow".into()))?;
        r.push(urls(&p.urls)?)
    }
    if sum != a.size_bytes {
        return Err(Error::Invalid("part sum differs".into()));
    }
    Ok(r)
}
async fn good(p: &Path, n: u64, h: &str) -> bool {
    let Ok(mut f) = tokio::fs::File::open(p).await else {
        return false;
    };
    let mut hasher = Sha256::new();
    let mut seen = 0u64;
    let mut b = [0u8; 65536];
    loop {
        let Ok(k) = f.read(&mut b).await else {
            return false;
        };
        if k == 0 {
            break;
        }
        seen = match seen.checked_add(k as u64) {
            Some(x) if x <= n => x,
            _ => return false,
        };
        hasher.update(&b[..k]);
    }
    seen == n && format!("{:x}", hasher.finalize()) == h
}
async fn get(t: &dyn Transport, us: &[Url], output: &Path, n: u64, h: &str) -> R<()> {
    for u in us {
        match t.get(u.clone()).await {
            Ok(mut body) => {
                let mut file = tokio::fs::File::create(output).await?;
                let mut hasher = Sha256::new();
                let mut seen = 0u64;
                let mut b = [0u8; 65536];
                let mut availability_failure = false;
                loop {
                    let room =
                        n.saturating_add(1).saturating_sub(seen).min(b.len() as u64) as usize;
                    let k = match body.read(&mut b[..room]).await {
                        Ok(k) => k,
                        Err(_) => {
                            availability_failure = true;
                            break;
                        }
                    };
                    if k == 0 {
                        break;
                    }
                    seen = seen.checked_add(k as u64).ok_or_else(|| {
                        Error::Integrity("pinned checksum or size mismatch".into())
                    })?;
                    if seen > n {
                        return Err(Error::Integrity("pinned checksum or size mismatch".into()));
                    }
                    hasher.update(&b[..k]);
                    file.write_all(&b[..k]).await?;
                }
                if availability_failure {
                    continue;
                }
                file.flush().await?;
                if seen != n || format!("{:x}", hasher.finalize()) != h {
                    return Err(Error::Integrity("pinned checksum or size mismatch".into()));
                }
                return Ok(());
            }
            Err(TransportError::Unavailable(_)) => {}
            Err(TransportError::Integrity(_)) => {
                return Err(Error::Integrity("pinned checksum or size mismatch".into()))
            }
        }
    }
    Err(Error::Unavailable)
}
static N: AtomicU64 = AtomicU64::new(0);
struct Tmp(PathBuf);
impl Drop for Tmp {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}
async fn tmp(dir: &Path) -> R<Tmp> {
    tokio::fs::create_dir_all(dir).await?;
    Ok(Tmp(dir.join(format!(
        ".materialize-{}.tmp",
        N.fetch_add(1, Ordering::Relaxed)
    ))))
}
/// Validates first; `Transport` receives only resolved, credential-free HTTPS URLs.
/// Caches are beside the destination, keyed by full SHA then part SHA.
pub async fn materialize(
    a: &Asset,
    destination: impl AsRef<Path>,
    trusted_base: Option<&str>,
    transport: &dyn Transport,
) -> R<PathBuf> {
    let groups = validate(a, trusted_base)?;
    let dest = destination.as_ref();
    let parent = dest.parent().unwrap_or(Path::new("."));
    let cache = parent.join(".manifest-materializer-cache");
    let full = cache.join("full").join(&a.sha256);
    let parts = cache.join("parts");
    if !good(&full, a.size_bytes, &a.sha256).await {
        let assembled = tmp(&cache).await?;
        if !a.urls.is_empty() {
            get(transport, &groups[0], &assembled.0, a.size_bytes, &a.sha256).await?
        } else {
            tokio::fs::create_dir_all(&parts).await?;
            let mut o = tokio::fs::File::create(&assembled.0).await?;
            for (p, us) in a.parts.iter().zip(&groups) {
                let hit = parts.join(&p.sha256);
                if !good(&hit, p.size_bytes, &p.sha256).await {
                    let x = tmp(&parts).await?;
                    get(transport, us, &x.0, p.size_bytes, &p.sha256).await?;
                    atomic_replace(&x.0, &hit).await?;
                    std::mem::forget(x)
                }
                let mut input = tokio::fs::File::open(hit).await?;
                tokio::io::copy(&mut input, &mut o).await?;
            }
            o.flush().await?
        }
        if !good(&assembled.0, a.size_bytes, &a.sha256).await {
            return Err(Error::Integrity(
                "full asset checksum or size mismatch".into(),
            ));
        }
        tokio::fs::create_dir_all(full.parent().unwrap()).await?;
        atomic_replace(&assembled.0, &full).await?;
        std::mem::forget(assembled)
    }
    let install = tmp(parent).await?;
    tokio::fs::copy(&full, &install.0).await?;
    atomic_replace(&install.0, dest).await?;
    std::mem::forget(install);
    Ok(dest.to_path_buf())
}
async fn atomic_replace(from: &Path, to: &Path) -> R<()> {
    #[cfg(not(windows))]
    {
        tokio::fs::rename(from, to).await?;
        Ok(())
    }
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        use windows_sys::Win32::Storage::FileSystem::{
            MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
        };
        let mut src: Vec<u16> = from.as_os_str().encode_wide().chain(Some(0)).collect();
        let mut dst: Vec<u16> = to.as_os_str().encode_wide().chain(Some(0)).collect();
        // SAFETY: both buffers are mutable, NUL-terminated UTF-16 paths and
        // remain alive for the duration of the call. REPLACE_EXISTING handles
        // both absent and concurrently-created destinations in one operation.
        for attempt in 0..20 {
            let ok = unsafe {
                MoveFileExW(
                    src.as_mut_ptr(),
                    dst.as_mut_ptr(),
                    MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
                )
            };
            if ok != 0 {
                return Ok(());
            }
            let error = std::io::Error::last_os_error();
            if attempt == 19 || !matches!(error.raw_os_error(), Some(5 | 32)) {
                return Err(Error::Io(error));
            }
            // Another verified-cache reader/writer can briefly hold the path
            // without delete sharing. Retry the same atomic primitive; never
            // delete the destination or expose a missing-file window.
            tokio::time::sleep(std::time::Duration::from_millis(2)).await;
        }
        Err(Error::Io(std::io::Error::other(
            "atomic replacement retry exhausted",
        )))
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;
    use std::{
        collections::HashMap,
        pin::Pin,
        sync::{
            atomic::{AtomicBool, AtomicU64},
            Arc, Mutex,
        },
        task::{Context, Poll},
    };
    fn sh(x: &[u8]) -> String {
        format!("{:x}", Sha256::digest(x))
    }
    fn a(d: &[u8], ps: Vec<&[u8]>) -> Asset {
        Asset {
            size_bytes: d.len() as u64,
            sha256: sh(d),
            urls: if ps.is_empty() {
                vec!["https://x/full".into()]
            } else {
                vec![]
            },
            parts: ps
                .into_iter()
                .enumerate()
                .map(|(i, p)| Part {
                    number: (i + 1) as u32,
                    size_bytes: p.len() as u64,
                    sha256: sh(p),
                    urls: vec![format!("https://x/{i}")],
                })
                .collect(),
        }
    }
    #[derive(Default)]
    struct T {
        d: Mutex<HashMap<String, std::result::Result<Vec<u8>, TransportError>>>,
        calls: Mutex<Vec<String>>,
    }
    #[async_trait]
    impl Transport for T {
        async fn get(&self, u: Url) -> std::result::Result<Body, TransportError> {
            self.calls.lock().unwrap().push(u.to_string());
            self.d
                .lock()
                .unwrap()
                .get(u.as_str())
                .cloned()
                .map(|r| r.map(|b| Box::new(Cursor::new(b)) as Body))
                .unwrap_or(Err(TransportError::Unavailable("no".into())))
        }
    }
    async fn r() -> PathBuf {
        let p = std::env::temp_dir().join(format!(
            "materialize-test-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        tokio::fs::create_dir_all(&p).await.unwrap();
        p
    }
    #[tokio::test]
    async fn direct_multipart_identity_and_part_cache() {
        assert!(FIXTURE_VECTORS.contains("multipart-reconstruction"));
        let root = r().await;
        let d = b"abcdef";
        let t = T::default();
        t.d.lock()
            .unwrap()
            .insert("https://x/full".into(), Ok(d.to_vec()));
        let o = root.join("o");
        materialize(&a(d, vec![]), &o, None, &t).await.unwrap();
        let m = a(d, vec![b"abc", b"def"]);
        t.calls.lock().unwrap().clear();
        materialize(&m, &o, None, &t).await.unwrap();
        assert!(t.calls.lock().unwrap().is_empty());
        assert_eq!(tokio::fs::read(o).await.unwrap(), d);
        // A layout change may miss the full cache but must still reuse verified
        // content-addressed parts without touching transport.
        let part_root = r().await;
        let multi = a(d, vec![b"abc", b"def"]);
        t.d.lock()
            .unwrap()
            .insert("https://x/0".into(), Ok(b"abc".to_vec()));
        t.d.lock()
            .unwrap()
            .insert("https://x/1".into(), Ok(b"def".to_vec()));
        materialize(&multi, part_root.join("first"), None, &t)
            .await
            .unwrap();
        let full = part_root
            .join(".manifest-materializer-cache/full")
            .join(&multi.sha256);
        tokio::fs::remove_file(full).await.unwrap();
        t.calls.lock().unwrap().clear();
        materialize(&multi, part_root.join("second"), None, &t)
            .await
            .unwrap();
        assert!(t.calls.lock().unwrap().is_empty());
    }
    #[tokio::test]
    async fn failover_checksum_destination_and_hostile() {
        let r = r().await;
        let mut x = a(b"abc", vec![]);
        x.urls = vec!["https://x/no".into(), "https://x/yes".into()];
        let t = T::default();
        t.d.lock()
            .unwrap()
            .insert("https://x/yes".into(), Ok(b"abc".to_vec()));
        let o = r.join("o");
        materialize(&x, &o, None, &t).await.unwrap();
        x.sha256 = "0".repeat(64);
        assert!(matches!(
            materialize(&x, &o, None, &t).await,
            Err(Error::Integrity(_))
        ));
        assert_eq!(tokio::fs::read(&o).await.unwrap(), b"abc");
        x.urls = vec!["../evil".into()];
        assert!(matches!(
            materialize(&x, &o, None, &t).await,
            Err(Error::Invalid(_))
        ))
    }
    #[tokio::test]
    async fn shared_vectors_deserialize_and_execute_transport_cases() {
        let fixtures: serde_json::Value = serde_json::from_str(FIXTURE_VECTORS).unwrap();
        for scenario in fixtures["scenarios"].as_array().unwrap() {
            let Some(asset_value) = scenario.get("asset") else {
                continue;
            };
            let asset: Asset = serde_json::from_value(asset_value.clone()).unwrap();
            if scenario["id"] == "cancellation" || scenario["id"] == "concurrency" {
                continue;
            }
            let transport = T::default();
            for response in scenario
                .get("responses")
                .into_iter()
                .flat_map(|x| x.as_array().into_iter().flatten())
            {
                transport.d.lock().unwrap().insert(
                    response["url"].as_str().unwrap().to_string(),
                    Ok(response["body"].as_str().unwrap().as_bytes().to_vec()),
                );
            }
            let root = r().await;
            let out = root.join("out");
            let answer = materialize(
                &asset,
                &out,
                scenario.get("base_url").and_then(|x| x.as_str()),
                &transport,
            )
            .await;
            match scenario["outcome"].as_str().unwrap() {
                "success" => assert_eq!(
                    tokio::fs::read(&out).await.unwrap().as_slice(),
                    if scenario["id"] == "multipart-reconstruction" {
                        &b"abcdef"[..]
                    } else {
                        &b"abc"[..]
                    }
                ),
                "integrity-error" => assert!(matches!(answer, Err(Error::Integrity(_)))),
                _ => unreachable!(),
            }
            let observed = transport.calls.lock().unwrap().clone();
            let expected: Vec<String> = scenario["expected_requests"]
                .as_array()
                .unwrap()
                .iter()
                .map(|x| x.as_str().unwrap().to_string())
                .collect();
            assert_eq!(observed, expected);
        }
    }
    struct RedirectTransport {
        locations: Vec<String>,
        calls: Mutex<Vec<String>>,
    }
    #[async_trait]
    impl Transport for RedirectTransport {
        async fn get(&self, initial: Url) -> std::result::Result<Body, TransportError> {
            let origin = initial.host_str().unwrap().to_string();
            let mut current = initial;
            self.calls.lock().unwrap().push(current.to_string());
            for (count, location) in self.locations.iter().enumerate() {
                if count >= MAX_REDIRECTS {
                    return Err(TransportError::Unavailable("redirect limit".into()));
                }
                let next = Url::parse(location)
                    .map_err(|_| TransportError::Unavailable("bad redirect".into()))?;
                if next.scheme() != "https" || next.host_str() != Some(&origin) {
                    return Err(TransportError::Unavailable("redirect origin".into()));
                }
                current = next;
                self.calls.lock().unwrap().push(current.to_string());
            }
            Ok(Box::new(Cursor::new(b"abc".to_vec())))
        }
    }
    #[tokio::test]
    async fn injected_transport_redirect_contract_is_executable() {
        let fixture: serde_json::Value = serde_json::from_str(FIXTURE_VECTORS).unwrap();
        assert_eq!(
            fixture["scenarios"]
                .as_array()
                .unwrap()
                .iter()
                .find(|x| x["id"] == "redirect-policy")
                .unwrap()["redirects"]["max_accepted"],
            5
        );
        let asset = a(b"abc", vec![]);
        let root = r().await;
        let good = RedirectTransport {
            locations: (0..5).map(|n| format!("https://x/r{n}")).collect(),
            calls: Mutex::new(vec![]),
        };
        materialize(&asset, root.join("good"), None, &good)
            .await
            .unwrap();
        assert_eq!(good.calls.lock().unwrap().len(), 6);
        let six = RedirectTransport {
            locations: (0..6).map(|n| format!("https://x/r{n}")).collect(),
            calls: Mutex::new(vec![]),
        };
        assert!(matches!(
            materialize(&asset, r().await.join("six"), None, &six).await,
            Err(Error::Unavailable)
        ));
        for bad in ["http://x/no", "https://other/no"] {
            let t = RedirectTransport {
                locations: vec![bad.into()],
                calls: Mutex::new(vec![]),
            };
            assert!(matches!(
                materialize(&asset, r().await.join("bad"), None, &t).await,
                Err(Error::Unavailable)
            ));
        }
    }
    #[test]
    fn signed_absolute_and_double_encoded_relative_urls() {
        assert!(url("https://x/file?X-Amz-Signature=private", None).is_ok());
        for raw in ["%252e%252e/x", "%255cfile", "x?private", "x#private"] {
            assert!(url(raw, Url::parse("https://x/base/").ok().as_ref()).is_err());
        }
    }
    struct Probe {
        bytes: Vec<u8>,
        consumed: Arc<AtomicU64>,
    }
    impl AsyncRead for Probe {
        fn poll_read(
            mut self: Pin<&mut Self>,
            _: &mut Context<'_>,
            buffer: &mut tokio::io::ReadBuf<'_>,
        ) -> Poll<std::io::Result<()>> {
            let n = self.bytes.len().min(buffer.remaining());
            if n == 0 {
                return Poll::Ready(Ok(()));
            }
            let chunk: Vec<u8> = self.bytes.drain(..n).collect();
            self.consumed.fetch_add(n as u64, Ordering::SeqCst);
            buffer.put_slice(&chunk);
            Poll::Ready(Ok(()))
        }
    }
    struct ProbeTransport {
        body: Vec<u8>,
        consumed: Arc<AtomicU64>,
    }
    #[async_trait]
    impl Transport for ProbeTransport {
        async fn get(&self, _: Url) -> std::result::Result<Body, TransportError> {
            Ok(Box::new(Probe {
                bytes: self.body.clone(),
                consumed: self.consumed.clone(),
            }))
        }
    }
    #[tokio::test]
    async fn oversize_vector_reads_exactly_declared_size_plus_one() {
        let v: serde_json::Value = serde_json::from_str(FIXTURE_VECTORS).unwrap();
        let scenario = v["scenarios"]
            .as_array()
            .unwrap()
            .iter()
            .find(|x| x["id"] == "oversize")
            .unwrap();
        let asset: Asset = serde_json::from_value(scenario["asset"].clone()).unwrap();
        let consumed = Arc::new(AtomicU64::new(0));
        let t = ProbeTransport {
            body: scenario["responses"][0]["body"]
                .as_str()
                .unwrap()
                .as_bytes()
                .to_vec(),
            consumed: consumed.clone(),
        };
        assert!(matches!(
            materialize(&asset, r().await.join("oversize"), None, &t).await,
            Err(Error::Integrity(_))
        ));
        assert_eq!(
            consumed.load(Ordering::SeqCst),
            scenario["abort_after"].as_u64().unwrap()
        );
    }
    struct PendingBody(Arc<AtomicBool>);
    impl AsyncRead for PendingBody {
        fn poll_read(
            self: Pin<&mut Self>,
            _: &mut Context<'_>,
            _: &mut tokio::io::ReadBuf<'_>,
        ) -> Poll<std::io::Result<()>> {
            self.0.store(true, Ordering::SeqCst);
            Poll::Pending
        }
    }
    struct PendingTransport(Arc<AtomicBool>);
    #[async_trait]
    impl Transport for PendingTransport {
        async fn get(&self, _: Url) -> std::result::Result<Body, TransportError> {
            Ok(Box::new(PendingBody(self.0.clone())))
        }
    }
    #[tokio::test]
    async fn cancellation_vector_cleans_temp_and_never_installs_final() {
        let v: serde_json::Value = serde_json::from_str(FIXTURE_VECTORS).unwrap();
        let scenario = v["scenarios"]
            .as_array()
            .unwrap()
            .iter()
            .find(|x| x["id"] == "cancellation")
            .unwrap();
        let asset: Asset = serde_json::from_value(scenario["asset"].clone()).unwrap();
        let root = r().await;
        let dest = root.join("out");
        let started = Arc::new(AtomicBool::new(false));
        let t = Arc::new(PendingTransport(started.clone()));
        let handle =
            tokio::spawn(async move { materialize(&asset, &dest, None, t.as_ref()).await });
        for _ in 0..100 {
            if started.load(Ordering::SeqCst) {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(1)).await
        }
        assert!(started.load(Ordering::SeqCst));
        handle.abort();
        assert!(handle.await.is_err());
        assert!(!root.join("out").exists());
        let cache = root.join(".manifest-materializer-cache");
        assert!(
            !cache.exists()
                || std::fs::read_dir(cache).unwrap().all(|e| !e
                    .unwrap()
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".materialize-"))
        );
    }
    #[tokio::test]
    async fn concurrency_vector_shares_cache_without_corrupting_destinations() {
        let v: serde_json::Value = serde_json::from_str(FIXTURE_VECTORS).unwrap();
        let scenario = v["scenarios"]
            .as_array()
            .unwrap()
            .iter()
            .find(|x| x["id"] == "concurrency")
            .unwrap();
        let asset: Asset = serde_json::from_value(scenario["asset"].clone()).unwrap();
        let t = T::default();
        for response in scenario["responses"].as_array().unwrap() {
            t.d.lock().unwrap().insert(
                response["url"].as_str().unwrap().into(),
                Ok(response["body"].as_str().unwrap().as_bytes().to_vec()),
            );
        }
        let root = r().await;
        let (one, two, three) = tokio::join!(
            materialize(&asset, root.join("one"), None, &t),
            materialize(&asset, root.join("two"), None, &t),
            materialize(&asset, root.join("three"), None, &t)
        );
        assert!(
            one.is_ok() && two.is_ok() && three.is_ok(),
            "{one:?} {two:?} {three:?}"
        );
        for name in ["one", "two", "three"] {
            assert_eq!(
                tokio::fs::read(root.join(name)).await.unwrap(),
                scenario["concurrency"]["expected_bytes"]
                    .as_str()
                    .unwrap()
                    .as_bytes()
            );
        }
    }
    struct LeakyBody;
    impl AsyncRead for LeakyBody {
        fn poll_read(
            self: Pin<&mut Self>,
            _: &mut Context<'_>,
            _: &mut tokio::io::ReadBuf<'_>,
        ) -> Poll<std::io::Result<()>> {
            Poll::Ready(Err(std::io::Error::other(
                "https://x/file?X-Amz-Signature=body-secret",
            )))
        }
    }
    struct LeakyTransport {
        body_error: bool,
    }
    #[async_trait]
    impl Transport for LeakyTransport {
        async fn get(&self, _: Url) -> std::result::Result<Body, TransportError> {
            if self.body_error {
                Ok(Box::new(LeakyBody))
            } else {
                Err(TransportError::Integrity(
                    "https://x/file?X-Amz-Signature=transport-secret".into(),
                ))
            }
        }
    }
    #[tokio::test]
    async fn signed_query_never_appears_in_transport_or_body_diagnostics() {
        let mut asset = a(b"abc", vec![]);
        asset.urls = vec!["https://x/file?X-Amz-Signature=request-secret".into()];
        for body_error in [false, true] {
            let error = materialize(
                &asset,
                r().await.join("out"),
                None,
                &LeakyTransport { body_error },
            )
            .await
            .unwrap_err();
            let diagnostic = error.to_string();
            assert!(!diagnostic.contains("Signature"));
            assert!(!diagnostic.contains("secret"));
            assert!(!diagnostic.contains('?'));
        }
    }
}
