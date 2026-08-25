# Tokio materializer reference

`materialize(&Asset, destination, trusted_base, &transport)` is the capability
level-2 reference for direct and multipart Assets. It validates all input before
giving resolved credential-free HTTPS URLs to the injected `Transport`.

The lockfile is intentionally committed: this runnable reference pins its small
dependency graph so verification behavior is reproducible. `target/` is ignored
and must not be committed.
