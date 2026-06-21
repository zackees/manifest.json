"""compile-for-target: produces a deterministic EmbeddedSlice."""

from __future__ import annotations

import hashlib
import json

import pytest

from manifest_json.compile import compile_for_target, serialize_slice
from manifest_json.resolve import AmbiguityError
from manifest_json.validate import validate_document


def test_compile_basic(catalog: dict, catalog_bytes: bytes) -> None:
    slice_doc = compile_for_target(
        catalog,
        platform={"os": "linux", "arch": "x86_64", "libc": "glibc"},
        channel="latest-stable",
        catalog_bytes=catalog_bytes,
    )
    assert slice_doc["kind"] == "EmbeddedSlice"
    assert slice_doc["tool"] == "clang"
    assert slice_doc["compiled_version"] == "21.1.5"
    assert slice_doc["compiled_for"]["channel"] == "latest-stable"
    assert slice_doc["compiled_for"]["platform"]["libc"] == "glibc"
    assert slice_doc["asset"]["sha256"] == "4021cc49d70472122761709e7376835dfc857b5ec77183fa969b5f61d0f13a2f"
    assert slice_doc["online_url"] == catalog["online_url"]
    assert slice_doc["online_sha256"] == hashlib.sha256(catalog_bytes).hexdigest()


def test_compile_output_validates(catalog: dict, catalog_bytes: bytes) -> None:
    slice_doc = compile_for_target(
        catalog,
        platform={"os": "linux", "arch": "x86_64", "libc": "glibc"},
        channel="latest-stable",
        catalog_bytes=catalog_bytes,
    )
    validate_document(slice_doc)


def test_compile_is_deterministic(catalog: dict, catalog_bytes: bytes) -> None:
    """Same input -> byte-identical output. The slice is meant to be committed."""
    slices = [
        compile_for_target(
            catalog,
            platform={"os": "linux", "arch": "x86_64", "libc": "glibc"},
            channel="latest-stable",
            catalog_bytes=catalog_bytes,
        )
        for _ in range(3)
    ]
    payloads = [serialize_slice(s) for s in slices]
    assert payloads[0] == payloads[1] == payloads[2]


def test_compile_size_under_2kb(catalog: dict, catalog_bytes: bytes) -> None:
    """Embedded slices must stay tiny — DESIGN.md §1 goal #3."""
    slice_doc = compile_for_target(
        catalog,
        platform={"os": "linux", "arch": "x86_64", "libc": "glibc"},
        channel="latest-stable",
        catalog_bytes=catalog_bytes,
    )
    payload = serialize_slice(slice_doc)
    assert len(payload) < 2048, f"slice grew to {len(payload)} bytes"


def test_compile_with_signing_pubkey(catalog: dict, catalog_bytes: bytes) -> None:
    slice_doc = compile_for_target(
        catalog,
        platform={"os": "linux", "arch": "x86_64", "libc": "glibc"},
        channel="latest-stable",
        signing_pubkey="ed25519:abc",
        catalog_bytes=catalog_bytes,
    )
    assert slice_doc["signing_pubkey"] == "ed25519:abc"


def test_compile_without_catalog_bytes_omits_online_sha(catalog: dict) -> None:
    slice_doc = compile_for_target(
        catalog,
        platform={"os": "linux", "arch": "x86_64", "libc": "glibc"},
        channel="latest-stable",
    )
    assert "online_sha256" not in slice_doc


def test_compile_propagates_resolve_errors(catalog: dict) -> None:
    with pytest.raises(AmbiguityError):
        compile_for_target(
            catalog,
            platform={"os": "linux", "arch": "x86_64"},
            channel="latest-stable",
        )


def test_compile_with_variant(multipart_release: dict) -> None:
    """The multipart_release example uses variant.edition=full; compile
    through a constructed Catalog wrapping it."""
    catalog = {
        "kind": "Catalog",
        "schema_version": 1,
        "tool": multipart_release["tool"],
        "online_url": "https://example.invalid/clang-extra/manifest.json",
        "channels": {"latest-stable": multipart_release["version"]},
        "releases": [
            {
                "version": multipart_release["version"],
                "published_at": multipart_release["published_at"],
                "urgency": "low",
                "min_client_version": 1,
                "platforms": multipart_release["platforms"],
            }
        ],
    }
    slice_doc = compile_for_target(
        catalog,
        platform={"os": "linux", "arch": "x86_64", "libc": "glibc"},
        channel="latest-stable",
        variant={"edition": "full"},
    )
    assert slice_doc["compiled_for"]["variant"] == {"edition": "full"}
    # Multi-part assets must round-trip
    assert len(slice_doc["asset"]["parts"]) == 3
