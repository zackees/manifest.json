"""Transport-polymorphic async httpx materializer reference."""
from __future__ import annotations
import hashlib, os, shutil, tempfile
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit
import httpx
from manifest_json.validate import ValidationError, _validate_asset_semantics

MAX_REDIRECTS = 5
class MaterializeError(RuntimeError): pass

def _verified(path: Path, size: int, digest: str) -> bool:
    if not path.is_file() or path.stat().st_size != size: return False
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""): h.update(b)
    return h.hexdigest()==digest

def resolve_url(url: str, base_url: str | None) -> str:
    """Resolve only validator-approved relatives against an explicit HTTPS base."""
    decoded=url
    for _ in range(8):
        next_decoded=unquote(decoded)
        if next_decoded == decoded: break
        decoded=next_decoded
    p=urlsplit(url); dp=urlsplit(decoded)
    relative=not p.scheme
    if (any(ord(c)<32 or ord(c)==127 for c in decoded) or "\\" in decoded
            or dp.fragment or any(x==".." for x in dp.path.split("/"))
            or (relative and ("%" in url or dp.query))):
        raise MaterializeError("unsafe URL")
    if not p.scheme:
        if not base_url: raise MaterializeError("relative URL requires trusted base_url")
        base=urlsplit(base_url)
        if base.scheme != "https" or not base.netloc: raise MaterializeError("trusted base_url must be HTTPS absolute")
        url=urljoin(base_url, url)
    p=urlsplit(url)
    if p.scheme != "https" or not p.netloc or p.username or p.password: raise MaterializeError("resolved URL must be credential-free HTTPS")
    return url

async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """Manual redirect policy works identically for injected and owned clients."""
    origin=urlsplit(url).netloc
    for _ in range(MAX_REDIRECTS + 1):
        r=await client.send(client.build_request("GET", url), follow_redirects=False, stream=True)
        if r.status_code not in {301,302,303,307,308}:
            final=urlsplit(str(r.url))
            if final.scheme != "https" or final.netloc != origin:
                await r.aclose(); raise MaterializeError("redirect changed trusted HTTPS origin")
            return r
        location=r.headers.get("location"); await r.aclose()
        if not location: raise MaterializeError("redirect missing location")
        url=resolve_url(urljoin(url,location), None)
        if urlsplit(url).netloc != origin: raise MaterializeError("redirect changed trusted origin")
    raise MaterializeError("redirect limit exceeded")

async def _fetch(client, urls, output, size, digest, base_url):
    last=None
    for candidate in urls:
        try:
            r=await _get(client, resolve_url(candidate, base_url))
            try:
                if r.status_code >= 400: raise httpx.HTTPStatusError("unavailable", request=r.request, response=r)
                h=hashlib.sha256(); written=0
                with output.open("wb") as f:
                    async for chunk in r.aiter_bytes(1<<20):
                        written += len(chunk)
                        # The moment size+1 is observed, stop consuming transport.
                        if written > size: raise MaterializeError("pinned checksum or size mismatch")
                        h.update(chunk); f.write(chunk)
                if written != size or h.hexdigest()!=digest: raise MaterializeError("pinned checksum or size mismatch")
                return
            finally: await r.aclose()
        except MaterializeError: raise
        except httpx.HTTPError: last=True
    raise MaterializeError("all mirrors unavailable")

async def materialize(asset, destination: Path, cache_dir: Path, client=None, *, base_url: str|None=None) -> Path:
    try: _validate_asset_semantics(asset,"asset")
    except ValidationError: raise MaterializeError("invalid asset metadata") from None
    cache_dir.mkdir(parents=True,exist_ok=True); destination.parent.mkdir(parents=True,exist_ok=True)
    full=cache_dir/"full"/asset["sha256"]; parts=cache_dir/"parts"; parts.mkdir(exist_ok=True)
    owned=client is None
    if owned: client=httpx.AsyncClient(headers={"Accept-Encoding":"identity"})
    try:
        if not _verified(full,asset["size_bytes"],asset["sha256"]):
            full.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.TemporaryDirectory(dir=cache_dir,prefix="materialize-") as name:
                tmp=Path(name); assembled=tmp/"assembled"
                if asset.get("urls"): await _fetch(client,asset["urls"],assembled,asset["size_bytes"],asset["sha256"],base_url)
                else:
                    with assembled.open("wb") as out:
                        for part in asset["parts"]:
                            hit=parts/part["sha256"]
                            if not _verified(hit,part["size_bytes"],part["sha256"]):
                                candidate=tmp/(part["sha256"]+".tmp"); await _fetch(client,part["urls"],candidate,part["size_bytes"],part["sha256"],base_url); os.replace(candidate,hit)
                            with hit.open("rb") as source: shutil.copyfileobj(source,out)
                if not _verified(assembled,asset["size_bytes"],asset["sha256"]): raise MaterializeError("full asset checksum or size mismatch")
                os.replace(assembled,full)
        fd,name=tempfile.mkstemp(dir=destination.parent,prefix=destination.name+".",suffix=".tmp"); os.close(fd)
        install=Path(name); shutil.copyfile(full,install); os.replace(install,destination); return destination
    finally:
        if owned: await client.aclose()
