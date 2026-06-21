"""compile-for-target: project a Catalog into a deterministic EmbeddedSlice.

See DESIGN.md §8.1.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from manifest_json.resolve import resolve_in_catalog


def _canonical_json_bytes(doc: dict[str, Any]) -> bytes:
    """Byte-stable JSON serialization for hashing."""
    return json.dumps(
        doc,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _strip_empty(d: Any) -> Any:
    """Drop empty strings, empty lists, empty dicts. Matches proto3 default
    omission so the slice stays tight."""
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            cleaned = _strip_empty(v)
            if cleaned in (None, "", [], {}):
                continue
            out[k] = cleaned
        return out
    if isinstance(d, list):
        return [_strip_empty(x) for x in d]
    return d


def compile_for_target(
    catalog: dict[str, Any],
    *,
    platform: dict[str, Any],
    channel: str,
    variant: dict[str, Any] | None = None,
    signing_pubkey: str = "",
    catalog_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Walk `catalog`, resolve the query, and emit an EmbeddedSlice dict.

    Output is deterministic (byte-stable across runs given identical inputs)
    so it can be committed and diffed cleanly.

    Args:
      catalog:        parsed Catalog document (dict).
      platform:       Platform tuple to compile for.
      channel:        Channel name (e.g. "latest-stable").
      variant:        Optional Variant tuple.
      signing_pubkey: Optional trust anchor for asset.signatures.
      catalog_bytes:  Raw bytes the Catalog was loaded from, used to
                      compute online_sha256 if `online_url` is set.
                      If None, online_sha256 is omitted.
    """
    asset = resolve_in_catalog(catalog, catalog["tool"], platform, channel, variant)

    channels = catalog.get("channels", {})
    version = channels[channel]

    slice_doc: dict[str, Any] = {
        "kind": "EmbeddedSlice",
        "schema_version": 1,
        "tool": catalog["tool"],
        "compiled_for": {
            "platform": _strip_empty(platform),
            "channel": channel,
        },
        "compiled_version": version,
        "asset": _strip_empty(asset),
    }
    if variant:
        cleaned_variant = _strip_empty(variant)
        if cleaned_variant:
            slice_doc["compiled_for"]["variant"] = cleaned_variant
    if signing_pubkey:
        slice_doc["signing_pubkey"] = signing_pubkey
    online_url = catalog.get("online_url", "")
    if online_url:
        slice_doc["online_url"] = online_url
        if catalog_bytes is not None:
            slice_doc["online_sha256"] = hashlib.sha256(catalog_bytes).hexdigest()

    return slice_doc


def serialize_slice(slice_doc: dict[str, Any]) -> bytes:
    """Deterministic serialization of an EmbeddedSlice for embedding.

    Pretty-printed with 2-space indent and stable key order — keeps the
    embedded blob diff-friendly when committed to source.
    """
    return (
        json.dumps(
            slice_doc,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


__all__ = ["compile_for_target", "serialize_slice"]
