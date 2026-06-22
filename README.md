# manifest.json

A codified `manifest.json` format for describing downloadable artifacts — asset
name, platform tuple, optional checksum, download URL, and links to other
manifests. One schema serves three audiences: humans reading the JSON, tools
consuming it programmatically, and binaries that embed a compiled per-target
slice of it.

The schema is defined in Protocol Buffers and projected 1:1 to JSON, so the
wire format gets proto's forward/backward compatibility guarantees while
remaining hand-editable.

---

## The contract

Every consumer reduces to this query:

```
resolve(tool, platform, channel, variant?) -> Asset
```

- **tool** — string ID (`"clang"`, `"soldr"`, `"zccache"`)
- **platform** — orthogonal tuple `{os, arch, os_version?, libc?, abi?, features?}`
- **channel** — symbolic (`"latest-stable"`, `"nightly"`, `"lts-22"`) **or** an exact version
- **variant** — optional orthogonal tuple `{optimization?, edition?, flavor?}`

Two platform/variant tuples are equal iff every present field is equal. A
missing field acts as a wildcard during resolution. This is the only way the
query works without parsing strings.

---

## Three-tier shape

```
Index               (federated; points at per-tool Catalogs)
  └── Catalog       (per tool; carries channel pointers + Release list)
        └── Release (per (tool, version); carries platform→Asset map)
              └── EmbeddedSlice  (compiled projection for one target)
```

Consumers fetch only what they need:

| Caller | Fetches |
|---|---|
| One-shot installer for the current host | `Index` → `Catalog` (2 small HTTP gets) |
| Multi-version manager listing all versions | `Index` → `Catalog` |
| Binary with baked-in resolution | `EmbeddedSlice` (compile-time `include_str!`) |
| Mirror builder / SBOM scanner | full tree walk |

Every cross-document link uses an OCI-style descriptor:

```json
{ "url": "...", "sha256": "...", "size_bytes": 4321 }
```

That gives integrity-chained federation across repos and CDNs.

---

## Example: embedded slice (< 1 KB, `include_str!`-able)

```json
{
  "kind": "EmbeddedSlice",
  "schema_version": 1,
  "tool": "clang",
  "compiled_for": {
    "platform": { "os": "linux", "arch": "x86_64", "libc": "glibc" },
    "variant": {},
    "channel": "latest-stable"
  },
  "compiled_version": "21.1.5",
  "asset": {
    "filename": "llvm-21.1.5-linux-x86_64.tar.zst",
    "media_type": "application/zstd",
    "size_bytes": 98921700,
    "sha256": "4021cc49d70472122761709e7376835dfc857b5ec77183fa969b5f61d0f13a2f",
    "urls": [
      "https://media.githubusercontent.com/media/zackees/clang-tool-chain-bins/main/assets/clang/linux/x86_64/llvm-21.1.5-linux-x86_64.tar.zst"
    ]
  },
  "online_url": "https://raw.githubusercontent.com/zackees/clang-tool-chain-bins/main/assets/clang/manifest.json",
  "online_sha256": "a7e3...."
}
```

A binary embedding this can resolve its tool URL with zero network calls,
verify integrity against the inline sha, and optionally fetch the live
`Catalog` later to detect drift via `online_sha256`.

---

## Example: Catalog (per tool)

```json
{
  "kind": "Catalog",
  "schema_version": 1,
  "tool": "clang",
  "online_url": "https://raw.githubusercontent.com/zackees/clang-tool-chain-bins/main/assets/clang/manifest.json",
  "channels": {
    "latest-stable": "21.1.5",
    "stable":        "21.1.5",
    "nightly":       "21.2.0-nightly+20260620",
    "lts-21":        "21.1.5"
  },
  "releases": [
    {
      "version": "21.1.5",
      "published_at": "2026-05-12T00:00:00Z",
      "urgency": "medium",
      "min_client_version": 1,
      "platforms": [
        {
          "platform": { "os": "linux", "arch": "x86_64", "libc": "glibc" },
          "variant":  {},
          "asset": {
            "filename": "llvm-21.1.5-linux-x86_64.tar.zst",
            "sha256":   "4021cc...",
            "size_bytes": 98921700,
            "urls": ["https://.../llvm-21.1.5-linux-x86_64.tar.zst"]
          }
        }
      ]
    }
  ]
}
```

Releases are sorted newest-first for human readability, but **symbolic
resolution always goes through `channels`** — never through array position.

---

## Example: Index (federated top-level)

```json
{
  "kind": "Index",
  "schema_version": 1,
  "tools": {
    "clang":  { "descriptor": { "url": "clang/manifest.json",  "sha256": "...", "size_bytes": 4321 } },
    "soldr":  { "descriptor": { "url": "soldr/manifest.json",  "sha256": "...", "size_bytes": 2100 } },
    "iwyu":   { "descriptor": { "url": "https://other-host/iwyu.json", "sha256": "...", "size_bytes": 1820 } }
  }
}
```

Sub-manifests can live in the same repo, a sibling repo, or a different CDN
— the `sha256` on each descriptor makes the chain verifiable end-to-end.

---

## Validate a manifest

Three options, ordered from most-complete to zero-install.

### 1. CLI (recommended)

```bash
pip install git+https://github.com/zackees/manifest.json
manifest-validate path/to/manifest.json [more.json ...]
```

```
OK   path/to/manifest.json
```

Exits non-zero on the first failure:

```
FAIL /tmp/bad.json: channel 'stable' -> '1.0', but no release with that version
```

### 2. Python API

```python
import json
from manifest_json import validate_document, ValidationError

doc = json.load(open("manifest.json"))
try:
    validate_document(doc)
except ValidationError as exc:
    print(f"invalid: {exc}")
```

`validate_document` returns `None` on success, raises `ValidationError` on any
structural or semantic violation. Suitable for CI scripts, pre-commit hooks,
and build-time checks.

### 3. Raw JSON Schema (any language, no Python package)

`manifest.schema.json` lives at the repo root, regenerated from
[`manifest.proto`](manifest.proto) on every commit. Feed it to any JSON Schema
2020-12 validator:

```python
import json, jsonschema
schema = json.load(open("manifest.schema.json"))
doc    = json.load(open("my-manifest.json"))
jsonschema.validate(doc, schema)
```

Same path works from JavaScript (Ajv), Go (gojsonschema), Rust (jsonschema
crate), etc.

**Caveat:** the raw schema catches only **structural** problems (field types,
unknown fields, `kind` discriminator). It does **not** catch semantic ones —
a Catalog whose `channels.stable` points at a version not present in
`releases[]` will pass JSON Schema validation but fail real-world use. For
full correctness use option 1 or 2.

| Check | JSON Schema (#3) | `validate_document` (#1, #2) |
|---|---|---|
| Field types, unknown fields, `kind` discriminator | ✅ | ✅ |
| Required top-level fields (`schema_version`, `tool`, ...) | ❌ | ✅ |
| `channels[name]` resolves to a real release `version` | ❌ | ✅ |
| `sha256` is 64-char lowercase hex | ❌ | ✅ |
| No duplicate `(platform, variant)` in a Release | ❌ | ✅ |
| `releases[]` sorted newest-first | ❌ | ✅ |
| `Source` declares `(repo_url + ref)` OR `archive_url` | ❌ | ✅ |
| `ArchiveContents.file_count` matches `len(files)` | ❌ | ✅ |
| File paths forward-slashed, no duplicates, valid type | ❌ | ✅ |
| symlinks/hardlinks require `linkname` | ❌ | ✅ |

---

## Python tooling

The `manifest-json` package ships four CLIs and a small Python API. All four
are installed by `pip install git+https://github.com/zackees/manifest.json`.

### CLIs (console scripts)

| Command | What it does | Example |
|---|---|---|
| `manifest-validate` | Validate one or more manifest files (structural + semantic). Exit 1 on first failure. | `manifest-validate path/to/manifest.json` |
| `manifest-resolve` | Resolve `(tool, platform, channel)` → one Asset (pretty JSON). With `--all`: every match as JSON Lines, ordered most-preferred first. | `manifest-resolve catalog.json --tool zccache --platform os=linux,arch=x86_64,libc=musl --channel latest-stable` |
| `manifest-compile` | Project a Catalog into a small `EmbeddedSlice` for one target. Output is deterministic — commit it and `include_str!` it into a binary. | `manifest-compile catalog.json --platform os=linux,arch=x86_64 --channel latest-stable -o slice.json` |
| `manifest-gen-schema` | Emit the canonical JSON Schema (Draft 2020-12) from the compiled `.proto` descriptors. | `manifest-gen-schema -o manifest.schema.json` |

### Python API

| Task | Import | Notes |
|---|---|---|
| **Validate a document** | `from manifest_json import validate_document, ValidationError` | Raises `ValidationError` — both structural (via JSON Schema) AND semantic (channel resolution, sha256 hex, etc.). See the table above. |
| **Resolve one asset** | `from manifest_json import resolve_in_catalog` | Returns one `Asset` dict. Raises `AmbiguityError` when the query matches more than one entry at top specificity. |
| **Resolve every matching asset** | `from manifest_json import resolve_all_in_catalog` | Returns an ordered `list[Resolution]`. Never raises on ambiguity. Use when your query is intentionally fuzzy (e.g. `arch="x86"` against a catalog with both `x86_64` and `i686`). |
| **Source-fallback resolve** | `from manifest_json import resolve_or_source` | Returns `Resolution(kind="binary"\|"source")`. Falls back to the Release's `source` field when no binary matches. |
| **Compile an embedded slice** | `from manifest_json import compile_for_target` | Returns a deterministic dict. Pair with `serialize_slice()` for byte-stable output. |
| **Generate the JSON Schema** | `from manifest_json import generate_json_schema` | Returns a Draft-2020-12 dict. Walks the compiled proto descriptors. |
| **Normalize a caller's platform tuple** | `from manifest_json.resolve import normalize_platform` | Maps caller-side aliases (`x64`/`amd64`/`mac`/`win`) to canonical values (`x86_64`/`darwin`/`windows`). |
| **Flatten a platform for filesystem paths** | `from manifest_json import flatten_platform` | `{os:darwin, arch:universal2}` → `"darwin-universal2"`. Use when laying out vendored binaries on disk. |

### Quick recipes

**Fetch a catalog and resolve for the current host:**

```python
import json, platform, urllib.request
from manifest_json import resolve_in_catalog

url = "https://zackees.github.io/soldr-toolchain/zccache/manifest.json"
cat = json.loads(urllib.request.urlopen(url).read())

asset = resolve_in_catalog(
    cat, "zccache",
    platform={"os": platform.system().lower(),
              "arch": platform.machine().lower(),
              "libc": "musl"},
    channel="latest-stable",
)
print(asset["urls"][0])     # the download URL
print(asset["sha256"])      # for integrity check after download
```

**List every match for an ambiguous query (JSON Lines for shell pipelines):**

```bash
manifest-resolve catalog.json \
  --tool exotic --platform os=linux,arch=x86 --channel latest-stable --all \
  | jq -r '.urls[0]'
```

**Compile a slice for embedding in a Rust binary:**

```bash
manifest-compile catalog.json \
  --platform os=linux,arch=x86_64,libc=glibc \
  --channel latest-stable \
  -o src/embedded_slice.json
# Then in Rust: const SLICE: &str = include_str!("embedded_slice.json");
```

### External tools worth knowing about

Some tasks have a better-suited tool than rolling our own. We recommend
these alongside the `manifest-json` package:

| Tool | Use it for | Why over our CLIs |
|---|---|---|
| **`check-jsonschema`** ([pypi](https://pypi.org/project/check-jsonschema/)) | Pre-commit hook + IDE integration | Auto-resolves the `$schema` URL on the document; richer error messages with json-path context; first-class `pre-commit` framework support. `pip install check-jsonschema && check-jsonschema path/to/manifest.json` works because every example carries `$schema`. Catches structural issues only — pair with `manifest-validate` for semantic. |
| **`jq`** (any package manager) | Ad-hoc shell-side catalog queries | Faster than spinning up Python for one-off lookups. `curl -s URL \| jq '.channels'` is muscle memory for ops. |
| **`grpcio-tools`** ([pypi](https://pypi.org/project/grpcio-tools/)) | Regenerating Python proto bindings | Bundles `protoc` cross-platform — no separate binary install. Already used by `ci/gen_proto.py`. |
| **`buf`** ([buf.build](https://buf.build/docs)) | Editing the `.proto` itself (lint, breaking-change detection) | Modern proto tooling; `buf lint` catches schema mistakes our build doesn't. |
| **VS Code JSON IntelliSense** (built-in) | Authoring manifests by hand | Detects `"$schema": "..."` and turns on auto-completion + inline validation. Zero config — works because our docs carry `$schema` pointing at the [published schema](https://zackees.github.io/manifest.json/v1/manifest.schema.json). |
| **Ajv CLI** ([ajv.js.org](https://ajv.js.org/packages/ajv-cli.html)) | Validation in Node.js / JS toolchains | Fastest JSON Schema validator on the planet. Same `manifest.schema.json` works as input. |
| **`gojsonschema`** ([Go](https://github.com/xeipuuv/gojsonschema)) | Validation in Go toolchains | Same schema URL; no Python needed. |

### When to use what

```
   ┌──────────────────────────────────────────────────────────┐
   │ I want to...                                             │
   ├──────────────────────────────────────────────────────────┤
   │ ...check a manifest is conformant     -> manifest-validate (semantic)
   │                                       or check-jsonschema (structural,
   │                                          pre-commit-friendly)
   │ ...find the asset for my host         -> manifest-resolve
   │ ...see ALL matches for a fuzzy query  -> manifest-resolve --all
   │ ...embed a tiny per-target slice      -> manifest-compile
   │ ...write the canonical schema to disk -> manifest-gen-schema
   │ ...inspect a catalog from a shell     -> curl ... | jq
   │ ...regenerate proto bindings          -> grpcio-tools (via ci/gen_proto.py)
   │ ...lint / breaking-change the proto   -> buf lint / buf breaking
   │ ...validate from another language     -> Ajv (JS), gojsonschema (Go),
   │                                          jsonschema crate (Rust)
   └──────────────────────────────────────────────────────────┘
```

---

## Use cases supported

| Case | How |
|---|---|
| GitHub Release artifact index | Attach a `Release` JSON to the release |
| Git-LFS-hosted tree | Hierarchical `Index → Catalog → Release` under `assets/<tool>/...` |
| Orphan `manifest` branch CDN | Tree on a long-lived branch served via `raw.githubusercontent.com` |
| Mirror / failover | `urls[]` array on every asset |
| Self-hosted / airgapped | URLs are relative or `base_url`-substitutable |
| Multi-version manager | `channels` map + full `releases[]` enumeration |
| Binary-embedded resolver | `EmbeddedSlice` produced by `compile-for-target` |
| Multi-part / chunked archives | `parts[]` with per-part sha+size+order |
| Component bundles | Group of assets sharing `(tool, version, platform)` |
| Supply chain attestations | `signatures[]`, `sbom_url`, `provenance_url` per asset |
| Yanked / deprecated | `yanked: true` + reason field |
| Compile-for-target | Tool walks tree, emits an `EmbeddedSlice` |

---

## Repo layout (planned)

```
.
├── README.md             # this file
├── DESIGN.md             # schema reference + rationale
├── proto/
│   └── manifest.proto    # canonical schema
├── examples/             # real-world JSON for each tier and use case
└── tools/                # compile-for-target, validators, diff
```

---

## Related repos using ad-hoc precursors of this format

- [zackees/soldr](https://github.com/zackees/soldr) — Rust toolchain wrapper; uses a `manifest`-branch CDN pattern (schema v5)
- [zackees/clang-tool-chain-bins](https://github.com/zackees/clang-tool-chain-bins) — LFS-hosted clang/LLVM/IWYU binaries; hierarchical `Index → mid → leaf` JSON
- [zackees/clang-tool-chain](https://github.com/zackees/clang-tool-chain) — Python consumer of the bins repo

This repo is the unified replacement.

---

## Status

Specification phase. See [DESIGN.md](DESIGN.md) for the schema, the resolution
algorithm, hosting modes, versioning rules, and a comparison with prior art.

## License

TBD.
