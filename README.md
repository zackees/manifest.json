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
