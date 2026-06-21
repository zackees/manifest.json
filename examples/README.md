# examples/

Reference manifests for every supported scenario. Every file in this tree is
validated by `ci/validate_examples.py` and exercised by `tests/`.

| File | Kind | Scenario |
|---|---|---|
| `index.json` | `Index` | Federated top-level pointing at multiple tool Catalogs (one in a sibling repo) |
| `catalog.json` | `Catalog` | Single-tool catalog with channels (`latest-stable`, `nightly`, `lts-21`) and multiple releases / platforms / variants |
| `github_release.json` | `Release` | Standalone release manifest as attached to a GitHub Release (with sig + sbom + provenance pointers) |
| `embedded_slice.json` | `EmbeddedSlice` | Tauri-shape compiled slice for one `(platform, channel)` with back-link to the canonical online Catalog |
| `multipart_release.json` | `Release` | Multi-part archive (`parts[]`) plus an optional component |
| `source_only_release.json` | `Release` | Source-only release: no prebuilt binaries, only a `source` field with VCS + archive + build hint |
| `hierarchical/manifest.json` | `Index` | Top-level index of a hierarchical tree |
| `hierarchical/clang/manifest.json` | `Catalog` | Sub-manifest under the hierarchical tree |
| `hierarchical/iwyu/manifest.json` | `Catalog` | Second sub-manifest in the same tree |

Validate everything:

```bash
python ci/validate_examples.py
```
