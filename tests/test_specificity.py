"""Specificity-priority resolution — the most-specific match wins.

Discovered as a needed feature during the soldr-toolchain v5->v1
migration: producers commonly ship both a bare `{os, arch}` entry as a
fallback AND explicit ABI/libc variants alongside it. With pure
wildcard semantics those collide ambiguously on every query that
specifies the disambiguator. Specificity-priority makes the explicit
match win unambiguously, mirroring OCI image-index and CSS-selector
conventions.
"""

from __future__ import annotations

import pytest

from manifest_json.resolve import AmbiguityError, resolve_in_catalog


def _catalog(platforms: list[dict]) -> dict:
    return {
        "kind": "Catalog",
        "schema_version": 1,
        "tool": "demo",
        "online_url": "https://example.invalid/demo/manifest.json",
        "channels": {"latest-stable": "1.0.0"},
        "releases": [
            {
                "version": "1.0.0",
                "published_at": "2026-01-01T00:00:00Z",
                "urgency": "low",
                "min_client_version": 1,
                "platforms": platforms,
            }
        ],
    }


def _platform(asset_name: str, **kw) -> dict:
    return {
        "platform": kw,
        "asset": {
            "filename": asset_name,
            "size_bytes": 1,
            "sha256": "a" * 64,
            "urls": [f"https://example.invalid/{asset_name}"],
        },
    }


def test_explicit_wins_over_bare_on_constrained_query() -> None:
    """Bare {linux, x86_64} + explicit musl variant; query with libc:musl
    must pick the explicit one, not bare-shadow-error."""
    cat = _catalog([
        _platform("bare.tar.gz",     os="linux", arch="x86_64"),
        _platform("musl-explicit.tar.gz", os="linux", arch="x86_64", libc="musl"),
    ])
    asset = resolve_in_catalog(
        cat, "demo",
        platform={"os": "linux", "arch": "x86_64", "libc": "musl"},
        channel="latest-stable",
    )
    assert asset["filename"] == "musl-explicit.tar.gz"


def test_bare_wins_when_query_unconstrained() -> None:
    """Query has no libc; bare entry is most-specific (the explicit musl
    entry doesn't contribute libc:musl to specificity because the query
    didn't ask for libc:musl)."""
    cat = _catalog([
        _platform("bare.tar.gz",          os="linux", arch="x86_64"),
        _platform("musl-explicit.tar.gz", os="linux", arch="x86_64", libc="musl"),
    ])
    # Tie at specificity 2 (os+arch on both) — still ambiguous because the
    # query doesn't prefer one over the other.
    with pytest.raises(AmbiguityError):
        resolve_in_catalog(
            cat, "demo",
            platform={"os": "linux", "arch": "x86_64"},
            channel="latest-stable",
        )


def test_three_way_specificity_chain() -> None:
    """Bare, libc-only, and (libc+abi)-tagged entries; querying with both
    libc and abi picks the most-specific."""
    cat = _catalog([
        _platform("bare.tar.gz",     os="linux", arch="x86_64"),
        _platform("musl-only.tar.gz", os="linux", arch="x86_64", libc="musl"),
        _platform("musl-llvm.tar.gz", os="linux", arch="x86_64", libc="musl", abi="gnullvm"),
    ])
    asset = resolve_in_catalog(
        cat, "demo",
        platform={"os": "linux", "arch": "x86_64", "libc": "musl", "abi": "gnullvm"},
        channel="latest-stable",
    )
    assert asset["filename"] == "musl-llvm.tar.gz"


def test_tied_specificity_is_genuine_ambiguity() -> None:
    """Two stored platforms that are EXACTLY as specific as the query
    for different fields — true ambiguity, must raise."""
    cat = _catalog([
        _platform("glibc.tar.gz", os="linux", arch="x86_64", libc="glibc"),
        _platform("musl.tar.gz",  os="linux", arch="x86_64", libc="musl"),
    ])
    with pytest.raises(AmbiguityError) as exc:
        resolve_in_catalog(
            cat, "demo",
            platform={"os": "linux", "arch": "x86_64"},
            channel="latest-stable",
        )
    assert len(exc.value.candidates) == 2


def test_query_field_absent_from_all_stored() -> None:
    """Query asks for features:[avx2] but no stored entry mentions
    features — query field present in 0 stored entries → no specificity
    contribution from features. Bare entry wins by being only one matching."""
    cat = _catalog([
        _platform("only.tar.gz", os="linux", arch="x86_64"),
    ])
    asset = resolve_in_catalog(
        cat, "demo",
        platform={"os": "linux", "arch": "x86_64", "features": ["avx2"]},
        channel="latest-stable",
    )
    assert asset["filename"] == "only.tar.gz"


def test_features_subset_match_specificity() -> None:
    """Stored with more matched features beats stored with fewer."""
    cat = _catalog([
        _platform("basic.tar.gz",  os="linux", arch="x86_64"),
        _platform("avx2.tar.gz",   os="linux", arch="x86_64", features=["avx2"]),
        _platform("avx512.tar.gz", os="linux", arch="x86_64", features=["avx2", "avx512"]),
    ])
    asset = resolve_in_catalog(
        cat, "demo",
        platform={"os": "linux", "arch": "x86_64", "features": ["avx2", "avx512"]},
        channel="latest-stable",
    )
    assert asset["filename"] == "avx512.tar.gz"


def test_real_world_crgx_scenario() -> None:
    """The exact pattern that triggered Bug #2 in the soldr-toolchain migration."""
    cat = _catalog([
        _platform("crgx-darwin-arm64.tar.gz",       os="darwin",  arch="aarch64"),
        _platform("crgx-darwin-x86_64.tar.gz",      os="darwin",  arch="x86_64"),
        _platform("crgx-linux-aarch64.tar.gz",      os="linux",   arch="aarch64"),
        _platform("crgx-linux-x86_64.tar.gz",       os="linux",   arch="x86_64"),
        _platform("crgx-linux-x86_64-musl.tar.gz",  os="linux",   arch="x86_64", libc="musl"),
        _platform("crgx-windows-x86_64.zip",        os="windows", arch="x86_64", abi="msvc"),
    ])
    # Query with explicit musl -> must hit the musl entry, not error.
    asset = resolve_in_catalog(
        cat, "demo",
        platform={"os": "linux", "arch": "x86_64", "libc": "musl"},
        channel="latest-stable",
    )
    assert asset["filename"] == "crgx-linux-x86_64-musl.tar.gz"

    # Query for bare linux-x86_64 -> still ambiguous (bare AND musl both
    # match at specificity 2, since the query didn't pick a libc).
    with pytest.raises(AmbiguityError):
        resolve_in_catalog(
            cat, "demo",
            platform={"os": "linux", "arch": "x86_64"},
            channel="latest-stable",
        )
