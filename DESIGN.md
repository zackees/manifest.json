# DESIGN.md — manifest.json schema

This document is the implementer's reference. See [README.md](README.md) for
the executive summary and quick examples.

---

## 1. Goals

1. **One schema for many hosting modes.** GitHub Releases, Git-LFS-hosted
   trees, orphan `manifest` branches, self-hosted CDNs, and binary-embedded
   slices all use the same proto/JSON shape.
2. **One query for all consumers.** Every read path reduces to
   `resolve(tool, platform, channel, variant?) -> Asset`.
3. **Tiny embedded slices.** A per-target projection must fit in well under
   2 KB and be deterministic so it commits cleanly into source.
4. **Human-readable JSON, machine-defined proto.** JSON is the wire format
   humans see; proto is the source of truth for field numbers, types, and
   forward-compat rules.
5. **Federation by content-addressing.** Sub-manifest links carry sha256 so
   a chain across repos/CDNs is verifiable end-to-end.

## 2. Non-goals

- **A package manager.** This is the *distribution manifest*, not install
  logic, dependency resolution, or version-range solving.
- **An RPC protocol.** Manifests are static documents fetched over plain
  HTTP(S). No client→server posting (Omaha-style); no cohorts; no
  staged-rollout server logic.
- **A signing standard.** We reference signatures and attestations via URLs
  (`signatures[]`, `sbom_url`, `provenance_url`); we do not invent or
  mandate a specific scheme.
- **A replacement for OCI registries.** Container images already have an
  excellent format. This is for everything else — toolchains, CLIs, runtime
  bundles, embeddable assets.

---

## 3. Design principles

### 3.1 Platform is a tuple, not a string

The single most common failure across surveyed formats: collapsing
`(os, arch, libc, abi, version)` into one ad-hoc token like Homebrew's
`arm64_sequoia` or Node.js's `osx-arm64-tar`. The cost is a parser in every
consumer and ambiguity at the edges (is `linux-x64-musl` a string or
a `(linux, x64, musl)` tuple?).

We adopt OCI's orthogonal model:

```proto
message Platform {
  string os           = 1;  // linux | darwin | windows | freebsd | wasi | ...
  string arch         = 2;  // x86_64 | aarch64 | armv7 | riscv64 | wasm32 | ...
  string os_version   = 3;  // optional; "minimum OS version"
  string libc         = 4;  // optional; glibc | musl | msvc | mingw
  string abi          = 5;  // optional; gnu | gnullvm | eabihf
  repeated string features = 6;  // optional; avx2, neon, sve, ...
}
```

**Equality rule:** two platform tuples are equal iff every field present in
*both* is equal. A field present in only one acts as a wildcard during
resolution. This is the basis of "give me clang for `(linux, x86_64)`" — the
caller doesn't have to know whether the manifest pins libc; if it does and
the caller didn't specify, resolution either picks the canonical one or
returns AmbiguityError (see §5.3).

### 3.2 Variant is its own axis, not a suffix

python-build-standalone's filename
`cpython-3.10.20+20260610-x86_64-unknown-linux-gnu-pgo+lto-full.tar.zst`
proves what happens when "variant" lives in the filename: every consumer
ships a regex. We separate it:

```proto
message Variant {
  string optimization = 1;  // pgo+lto | size | debug | ...
  string edition      = 2;  // full | minimal | install_only | install_only_stripped
  string flavor       = 3;  // distribution-defined free-form
}
```

Same wildcard semantics as Platform.

### 3.3 Channels are first-class, not derived

Symbolic heads (`latest-stable`, `nightly`, `lts-22`) live on the `Catalog`
as a `map<string, string>` from channel name to version. Two reasons:

- Eliminates the "what does `releases[0]` mean?" ambiguity that Node.js's
  index leaves implicit.
- Lets symbolic and exact-version queries share one resolve path —
  `channels["21.1.5"] = "21.1.5"` is a no-op for direct version lookups.

A channel name is just a string. We standardize a recommended vocabulary
but the schema accepts arbitrary names.

**Recommended channel vocabulary** (informative, not enforced):

| Name | Meaning |
|---|---|
| `latest-stable` | newest non-prerelease |
| `stable` | alias for `latest-stable` (current convention) |
| `nightly` | newest dated nightly build |
| `latest` | newest of *any* kind (may be a prerelease) |
| `beta` | newest beta |
| `rc` | newest release candidate |
| `lts-N` | newest in long-term-support line N (e.g. `lts-22`) |
| `edge` | newest dev/canary |
| `<exact-version>` | exact match; required to round-trip |

### 3.4 Cross-document references are OCI descriptors

Every link to a sub-manifest or asset uses the same shape:

```proto
message Descriptor {
  string url        = 1;
  string sha256     = 2;
  uint64 size_bytes = 3;
  string media_type = 4;  // optional; "application/vnd.manifest.v1+json"
}
```

Gives us, for free:
- Content-addressed integrity across federated sub-manifests
- Mirror fallback via additional URL fields where appropriate
- The same primitive for "link to a JSON sub-manifest" and "link to a binary
  asset," reducing the number of concepts in the schema

### 3.5 One proto, projected to JSON

JSON field names use `snake_case` to match the proto (no implicit
camelCase rewriting). Enums project to lowercase strings. Empty/default
fields are omitted from JSON output, matching proto3 default semantics.

This lets `protoc --decode` and `jq` operate on the same data without
extra tooling.

---

## 4. Schema reference

The full proto lives at `manifest.proto` (repo root). Below is a tour of the
message types, grouped by tier.

### 4.1 Shared primitives

```proto
message Platform { /* see §3.1 */ }
message Variant  { /* see §3.2 */ }

message Descriptor {
  string url        = 1;
  string sha256     = 2;
  uint64 size_bytes = 3;
  string media_type = 4;
}

message Asset {
  string filename       = 1;
  string media_type     = 2;
  uint64 size_bytes     = 3;
  string sha256         = 4;
  repeated string urls  = 5;            // primary + mirrors
  repeated Part parts   = 6;            // empty unless multi-part
  repeated Signature signatures = 7;
  string sbom_url       = 8;
  string provenance_url = 9;
  bool   yanked         = 10;
  string yanked_reason  = 11;
  repeated string provides     = 12;    // entry-point binaries inside this archive
  Descriptor contents_manifest = 13;    // optional pointer to an ArchiveContents doc
}

message Part {
  uint32 number      = 1;   // 1-indexed
  string sha256      = 2;
  uint64 size_bytes  = 3;
  repeated string urls = 4;
}

message Signature {
  string type = 1;          // cosign | minisign | gpg | ssh
  string url  = 2;          // detached sig location
  string key_hint = 3;      // optional pubkey identifier
}
```

### 4.2 Tier 1 — Index (federated top-level)

```proto
message Index {
  string kind           = 1;  // "Index"
  uint32 schema_version = 2;  // currently 1
  map<string, ToolEntry> tools = 3;
}

message ToolEntry {
  Descriptor descriptor = 1;  // points at the per-tool Catalog
  string     summary    = 2;  // optional one-line description
  string     kind_hint  = 3;  // free-form category: "tool" | "sdk" | "sysroot" | ...
}
```

**`ToolEntry.kind_hint`** is a free-form, producer-defined category string.
The validator does NOT enforce a controlled vocabulary. It lets an Index
distinguish an invocable binary from an SDK / sysroot / library / data
bundle when a UI or installer cares about that distinction. Suggested
values: `"tool"`, `"sdk"`, `"sysroot"`, `"library"`, `"data"`. Empty or
missing means "no claim" — consumers fall back to the `summary` text.

### 4.3 Tier 2 — Catalog (per tool)

```proto
message Catalog {
  string kind           = 1;  // "Catalog"
  uint32 schema_version = 2;
  string tool           = 3;
  string online_url     = 4;  // self-pointer (canonical URL of this doc)
  map<string, string> channels = 5;  // channel name -> version
  repeated Release releases = 6;     // sorted newest-first
}
```

### 4.4 Tier 3 — Release (per version)

```proto
message Release {
  string kind                        = 1; // "Release" when standalone
  uint32 schema_version              = 2;
  string tool                        = 3;
  string version                     = 4;
  string published_at                = 5;     // RFC 3339
  string urgency                     = 6;     // low | medium | high | critical (AppStream)
  uint32 min_client_version          = 7;     // gates older embedded readers
  repeated ReleasePlatform platforms = 8;
  repeated Component components      = 9; // optional component bundles
  Source source                      = 10;    // canonical source; fallback when no binary matches
}

message Source {
  string vcs                = 1;     // git | hg | svn | ""
  string repo_url           = 2;
  string ref                = 3;     // git tag / commit
  string archive_url        = 4;     // pre-packaged source tarball (e.g. GitHub auto-generated)
  string archive_sha256     = 5;
  uint64 archive_size_bytes = 6;
  string build_command      = 7;     // free-form hint, NOT a recipe
  repeated Platform supported_platforms = 8;  // informational; empty = "no claim either way"
  repeated Signature signatures = 9;
}

message ReleasePlatform {
  Platform platform = 1;
  Variant  variant  = 2;
  Asset    asset    = 3;
}

message Component {
  string id       = 1;     // "clang-extra", "sysroot", "headers", ...
  bool   required = 2;
  Asset  asset    = 3;
  Platform platform = 4;
  Variant  variant  = 5;
}
```

### 4.5 ArchiveContents (per-archive file listing)

Optional companion document linked from `Asset.contents_manifest`.
Describes the file tree inside one archive — the same information the
clang-tool-chain-bins repo already publishes as sidecar `.tar.zst.json`
files. Verifiable via `asset_sha256` (must equal the linked Asset's
sha256).

```proto
message ArchiveContents {
  string   kind           = 1;  // "ArchiveContents"
  uint32   schema_version = 2;
  string   asset_sha256   = 3;  // back-link to Asset.sha256
  uint64   file_count     = 4;
  repeated ArchiveFile files = 5;
}

message ArchiveFile {
  string path     = 1;  // forward-slashed, relative to archive root
  string type     = 2;  // file | dir | symlink | hardlink
  uint64 size     = 3;
  uint32 mode     = 4;  // POSIX permission bits
  string linkname = 5;
  string sha256   = 6;
}
```

Two-level scheme:

- `Asset.provides[]` (flat strings, kept inline) answers the cheap query
  "does this archive ship `clang-tidy`?" with zero extra fetches.
- `Asset.contents_manifest` (a `Descriptor` pointing at an
  `ArchiveContents` doc) lets a consumer who needs the full file tree
  fetch it on demand — without bloating the parent Catalog or the
  embedded slice.

The compile-for-target tool drops `contents_manifest` from
EmbeddedSlice output by default and keeps `provides[]`.

### 4.6 Tier 4 — EmbeddedSlice (compiled for one target)

```proto
message EmbeddedSlice {
  string  kind            = 1;  // "EmbeddedSlice"
  uint32  schema_version  = 2;
  string  tool            = 3;
  CompiledFor compiled_for = 4;
  string  compiled_version = 5;
  Asset   asset           = 6;
  string  signing_pubkey  = 7;  // optional; trust anchor for `asset.signatures`
  string  online_url      = 8;  // back-link to canonical Catalog
  string  online_sha256   = 9;  // sha of Catalog at compile time
}

message CompiledFor {
  Platform platform = 1;
  Variant  variant  = 2;
  string   channel  = 3;        // the channel that resolved to compiled_version
}
```

---

## 5. Resolution algorithm

### 5.1 Inputs

- A starting URL (Index or Catalog) — typically baked into the consumer.
- A query: `(tool, platform, channel, variant?)`.

### 5.2 Steps

```
1. doc <- fetch(starting_url)
2. if doc.kind == "Index":
       entry <- doc.tools[tool]              // KeyError -> ToolNotFound
       catalog <- fetch_verified(entry.descriptor)
   elif doc.kind == "Catalog":
       catalog <- doc
       assert catalog.tool == tool
   else:
       raise SchemaError

3. version <- catalog.channels[channel]      // KeyError -> ChannelNotFound
4. release <- find(catalog.releases, r => r.version == version)
                                             // None -> VersionNotInCatalog
5. matches <- [
       (specificity(rp), rp) for rp in release.platforms
       if platform_matches(rp.platform, platform)
       and (variant is None or variant_matches(rp.variant, variant))
   ]
   # Keep only the maximum-specificity candidates (CSS-selector style).
   best   <- max(s for s, _ in matches)
   winners <- [rp for s, rp in matches if s == best]

6. if len(matches)  == 0: raise NoMatchingAsset
   if len(winners) == 1: return winners[0].asset
   if len(winners) >  1: raise AmbiguityError(winners)
```

**Specificity** counts the fields present in *both* `stored.platform` and the
query that are equal — i.e. the dimensions on which the producer explicitly
constrained AND the caller explicitly asked. This lets producers publish a
bare `{os, arch}` fallback alongside explicit `libc:musl` / `abi:msvc`
variants without the bare entry shadow-matching every constrained query.
The convention mirrors OCI image-index and CSS-selector specificity.

**Query-side alias normalization.** Callers come from many ecosystems with
incompatible naming conventions for the same architecture (`x64` in npm,
`amd64` in Debian/Docker, `x86_64` in Rust target triples, casual `x86`).
The resolver normalizes the caller's query through a fixed alias map before
matching, so any of those queries resolve to the canonical `x86_64` stored
entry:

| Canonical | Recognized aliases |
|---|---|
| `x86_64` | `x86_64`, `x64`, `amd64`, `x86-64`, `x86` |
| `aarch64` | `aarch64`, `arm64`, `arm` |
| `armv7` | `armv7`, `armhf`, `armv7l` |
| `i686` | `i686`, `i386`, `x86_32` (kept distinct from x86_64) |
| `riscv64` | `riscv64`, `rv64` |
| `wasm32` | `wasm32`, `wasm` |
| OS `darwin` | `darwin`, `macos`, `mac`, `osx`, `macosx` |
| OS `windows` | `windows`, `win`, `win32`, `win64` |

Alias matching is case-insensitive. Unknown values pass through unchanged
and still get equality-compared.

**Producer-side values are NOT normalized.** If a producer mistakenly
publishes `arch: "arm64"` instead of `aarch64`, the resolver doesn't
silently fix it — the manifest fails to match any query and the bug
surfaces. Validators can additionally enforce canonical values on the
stored side.

Note on `x86`: 32-bit Intel was historically called `x86` and modern 64-bit
Intel desktop chips are `x86_64`/`amd64`. We normalize `x86 → x86_64`
because in 2026 essentially nobody asking for "x86" wants the 32-bit
variant. Genuine 32-bit users spell it `i686`/`i386`/`x86_32` (all of
which map to `i686`, distinct from `x86_64`).

**Universal-arch compatibility** (`arch: "universal2"` or `arch: "universal"`)
is an asymmetric exception to the strict arch equality check. On darwin
only, a stored universal-arch entry matches a query for any concrete arch
(`x86_64`, `aarch64`) — letting producers publish a fat Mach-O once instead
of duplicating it under both concrete-arch keys. The match contributes 0 to
specificity (only the os check counts), so an explicit `arch: "x86_64"`
entry always wins over a universal entry when both are present. The compat
is one-way: a pure `arch: "x86_64"` entry does NOT satisfy a query asking
for `arch: "universal2"`. Universal-arch on non-darwin OSes has no special
meaning — equality applies as normal.

### 5.3 Source fallback — `resolve_or_source`

When a resolver needs binary-or-source-fallback semantics (DESIGN.md §3.1
goal: "no binary for this platform → build from source"), it uses
`resolve_or_source` instead of `resolve_in_catalog`:

```
1. Try resolve_in_catalog(...). If it returns an Asset, return
   Resolution(kind="binary", asset=...).
2. If it raised NoMatchingAssetError AND release.source is present,
   return Resolution(kind="source", source=...).
3. If release.source is absent, raise NoBinaryOrSourceError.
4. Other resolver errors (ChannelNotFound, Ambiguity, ...) propagate
   unchanged — falling back to source on Ambiguity would mask a
   producer bug.
```

Source is modeled after PyPI's sdist + wheels: source is the
authoritative-and-typically-present thing, binaries are per-platform
optimizations. A consumer that can't run the prebuilt binary (no glibc
compat, exotic arch, security policy that forbids prebuilts) gets a
deterministic discovery path instead of scraping the GitHub Releases
page.

### 5.4 Matching semantics

`platform_matches(stored, query)`:

- For each field present in `query`, `stored` must have an equal value or
  not have that field at all (wildcard).
- For each field present in `stored` but not in `query`, no constraint.

Same rule for variants.

**AmbiguityError** carries the list of matching candidates so the caller
can decide. (E.g. "I asked for `(linux, x86_64)` and got both `glibc` and
`musl` builds — pick one and re-resolve with `libc` specified.")

### 5.5 Caching and verification

- `Descriptor.sha256` MUST be verified on every fetch of a sub-manifest or
  asset.
- `EmbeddedSlice.online_sha256` is informational; it lets a running binary
  detect "my embedded catalog is stale" without trusting a flag, but is not
  itself a security boundary.

---

## 6. Hosting modes

The schema is hosting-agnostic. Five proven layouts:

### 6.1 GitHub Releases artifact index

Attach a single `manifest.json` (kind: `Release`) to each GitHub Release.
Consumers GET the release page's `assets[].browser_download_url` for
`manifest.json` and skip the GH API entirely. No platform/file regex,
no rate limit.

### 6.2 Git-LFS-hosted tree

Big binaries behind LFS, manifests as plain raw-text files served by
`media.githubusercontent.com` (LFS-backed) and `raw.githubusercontent.com`
(plain). Tree shape:

```
assets/
  <tool>/manifest.json                        # Catalog
  <tool>/<os>/<arch>/<version>.tar.zst        # binaries (LFS)
```

This is the [clang-tool-chain-bins](https://github.com/zackees/clang-tool-chain-bins)
pattern, generalized.

### 6.3 Orphan `manifest` branch CDN

A long-lived orphan branch whose tree is *only* the manifest hierarchy.
A nightly builder script regenerates the tree and commits the diff;
consumers read via `raw.githubusercontent.com/<owner>/<repo>/manifest/...`.

Two advantages over Releases-API consumption:
- CDN-cached, no auth required, no GH API rate limit.
- Per-file diffs in Git history — a release that didn't actually change is
  a no-op commit.

This is the [soldr](https://github.com/zackees/soldr) pattern.

### 6.4 Self-hosted / airgapped

`Asset.urls[]` can be relative or substitutable. Recommended convention:
the first URL is canonical, additional URLs are mirrors. A bootstrap tool
can rewrite all URLs through a single `base_url` for an internal mirror.

### 6.5 Binary-embedded

`compile-for-target` (see §8) projects a Catalog → EmbeddedSlice for one
`(platform, variant, channel)`. The slice is small enough to embed via
`include_str!` (Rust) or `embed.FS` (Go), and carries an `online_url` back
to the canonical Catalog for opt-in refresh.

---

## 7. Multi-part archives

Single asset spanning multiple files (e.g. >2 GB or LFS-quota constrained):

```json
{
  "filename":   "clang-21.1.5-linux-x86_64.tar.zst",
  "sha256":     "<sha of full reconstructed archive>",
  "size_bytes": 4500000000,
  "parts": [
    { "number": 1, "sha256": "...", "size_bytes": 1500000000, "urls": ["..."] },
    { "number": 2, "sha256": "...", "size_bytes": 1500000000, "urls": ["..."] },
    { "number": 3, "sha256": "...", "size_bytes": 1500000000, "urls": ["..."] }
  ]
}
```

Reconstruction is byte concatenation in `number` order. The top-level
`sha256` is over the reconstructed whole; part `sha256` values let the
downloader verify each part independently and resume.

---

## 8. Tooling

The repo will ship reference implementations of:

### 8.1 `compile-for-target`

Walks an Index/Catalog tree, resolves a single `(tool, platform, channel,
variant?)` query, and emits an EmbeddedSlice. Output is deterministic
(byte-stable across runs given identical inputs) so the slice can be
committed and diffed.

### 8.2 `validate`

Checks that a manifest tree is well-formed:
- All descriptor sha256 values match the referenced docs
- No duplicate `(platform, variant)` within a Release
- Every `channels[name]` resolves to an entry in `releases[]`
- Sort order is correct (releases newest-first)

### 8.3 `diff`

Pretty-prints the difference between two snapshots of the same manifest
tree, useful for release announcements and mirror-sync logs.

---

## 9. Versioning rules

`schema_version` is a `uint32` on every top-level document type
(`Index`, `Catalog`, `Release`, `EmbeddedSlice`). Bump it only on
*breaking* changes — see proto rules below.

### 9.1 Backward compatibility (new readers, old files)

- Always allowed: readers MUST tolerate unknown fields (proto3 default).
- Always allowed: readers MUST tolerate missing optional fields.
- Old `schema_version` is a request for old semantics; readers may either
  upgrade in-memory or refuse.

### 9.2 Forward compatibility (old readers, new files)

- New fields: always optional, never required by old readers to make sense
  of the document.
- Field numbers are append-only — never reuse a tag number.
- Enum values: add new variants at the end; never reorder.
- Renames are forbidden in the proto; rename in code, keep proto identifier.

### 9.3 Breaking changes

A `schema_version` bump is required for:
- Removing or renaming a field
- Changing a field's type
- Changing the meaning of an existing value
- Changing the resolution algorithm semantics

Old documents with a lower `schema_version` MUST still be readable
indefinitely.

---

## 10. Prior art comparison

This schema is a synthesis of patterns proven by other systems. The table
below shows which ideas we adopted, modified, or rejected. Detailed
research notes are in the project history.

### 10.1 Adopted (with attribution)

| Idea | Source | How we use it |
|---|---|---|
| Orthogonal `{os, architecture, variant, os.version, os.features[]}` | OCI Image Index | `Platform` message |
| Descriptor `{mediaType, digest, size, urls[]}` for every link | OCI | `Descriptor` message |
| `lib_c_type`, `term_of_support` as explicit dimensions | Foojay Disco JDK API | `libc`, channel vocabulary |
| Channel-as-symbolic-pointer | Sparkle `<sparkle:channel>`, rustup channel files | `Catalog.channels` map |
| Tiered serving (slim "latest" alongside full catalog) | conda `current_repodata.json` | EmbeddedSlice + Catalog split |
| Minimal embedded JSON with platforms-map + back-link | Tauri `latest.json` | `EmbeddedSlice` shape |
| `urgency` + per-arch artifact tuple | AppStream `<releases>` | `Release.urgency`, `ReleasePlatform` |
| `schema_version` field + dual-compression hash slots | rustup v2 manifest | `schema_version` field + multi-URL `Asset` |
| Multi-part archive via array of parts | scoop `url`/`hash` arrays | `Asset.parts[]` |
| Sorted newest-first array of releases, channel resolves separately | soldr `manifest` branch v5 | `Catalog.releases[]` |
| Hierarchical sub-manifest tree per tool / os / arch | clang-tool-chain-bins | `Index → Catalog → Release` |

### 10.2 Modified

| Idea | Source | What we changed |
|---|---|---|
| Per-release `assets[]` map | GitHub Releases API | Replaced free-form `assets[]` with typed `ReleasePlatform[]` — same data, parseable shape |
| Component bundles | rustup `pkg.<name>.target.<triple>` | Flattened into `Release.components[]` with explicit `(platform, variant)` keys instead of nested map |

### 10.3 Explicitly rejected

| Pattern | Source | Why rejected |
|---|---|---|
| Flat platform strings (`arm64_sequoia`, `osx-arm64-tar`) | Homebrew, Node.js | Unparseable without a per-format lookup table; defeats the resolve query |
| Platform/variant encoded in filename suffix | python-build-standalone | Forces every consumer to ship a regex |
| Monolithic catalog (`index.yaml` 50 MB at scale) | Helm | Doesn't scale; hostile to embedded use |
| Per-tool shell scripts as "the manifest" | asdf-vm | Externalizes URLs/checksums/platform logic to N untrusted scripts |
| SHA-1 + positional plain text + no platform field | Squirrel.Windows `RELEASES` | Weak hash, ambiguous parsing, no extensibility |
| Bidirectional XML-RPC with cohorts | Omaha (Chrome) | Manifests should be static documents; cohorts require server logic |
| XML with arch-as-folder-convention | Chocolatey `.nuspec` | Wrong primitive for prebuilt cross-platform binaries |
| `lts: "Iron" \| false` mixed-type fields | Node.js index.json | Type confusion; channel data should not double as boolean flags |
| Bottle-style `formula.json` catalog (one giant doc) | Homebrew | Forces clients to download the world on every refresh |

---

## 11. Open questions

These need a decision before v1 is locked.

1. **Should `Channel` be a typed message instead of a string?** Adding
   metadata (e.g. "this channel auto-yanks entries older than N") would
   need a message; today the simple `map<string, string>` covers all known
   use cases. Default: keep string.
2. **Required vs. optional `signing_pubkey` on EmbeddedSlice.** Strongest
   security posture is required, but it forces all producers to integrate
   signing. Default: optional, with a `validate` warning if absent.
3. **Should `Component.required` default to true or false?** rustup
   defaults to "profile decides"; we don't have profiles. Default: false
   (additive components are the common case).
4. **Free-form `flavor` vs. enum.** Today `Variant.flavor` is a free string
   for distro-specific tags. Risk: drift. Mitigation: validate doc tracks
   known values as informative vocabulary, not enforced.
5. **Path separator in nested manifest URLs.** clang-tool-chain-bins's
   sidecars currently leak `\` on Windows. Rule: schema mandates forward
   slashes; producers must normalize.
6. **`min_client_version` semantics.** Does it gate the reader version
   (schema interpretation) or the embedded slice's reader (binary
   capability)? Default: the reader of the document containing the field.

## 12. References

- OCI Image Index spec: <https://github.com/opencontainers/image-spec/blob/main/image-index.md>
- Tauri updater: <https://v2.tauri.app/plugin/updater/>
- Sparkle appcast: <https://sparkle-project.org/documentation/publishing/>
- AppStream releases: <https://www.freedesktop.org/software/appstream/docs/>
- rustup channel manifest v2: <https://github.com/rust-lang/rustup/blob/master/doc/dev-guide/src/contributing/release-architecture.md>
- Foojay Disco API: <https://api.foojay.io/swagger-ui/index.html>
- Conda repodata: <https://docs.conda.io/projects/conda/en/latest/user-guide/concepts/pkg-specs.html>
- AppStream: <https://www.freedesktop.org/software/appstream/docs/>
- soldr manifest builder: <https://github.com/zackees/soldr/blob/main/.github/scripts/build_manifest.py>
- clang-tool-chain-bins layout: <https://github.com/zackees/clang-tool-chain-bins/tree/main/assets>
