"""resolve_all_in_catalog: multi-result search for ambiguous queries.

When a query like `arch: "x86"` could mean either x86_64 (modern
desktop) or i686 (legacy 32-bit), resolve_all_in_catalog returns
BOTH matches in priority order (x86_64 first), letting the caller
disambiguate visually instead of getting an AmbiguityError.

Same for `arch: "arm"` (aarch64 vs armv7).

Single-canonical aliases (`x64`, `amd64`) still return one result.
"""

from __future__ import annotations

import pytest

from manifest_json.resolve import (
    AmbiguityError,
    NoMatchingAssetError,
    _expand_arch,
    resolve_all_in_catalog,
    resolve_in_catalog,
)


def _catalog(platforms):
    return {
        "kind": "Catalog",
        "schema_version": 1,
        "tool": "demo",
        "online_url": "https://example.invalid/demo/manifest.json",
        "channels": {"latest-stable": "1.0.0"},
        "releases": [{
            "version": "1.0.0",
            "published_at": "2026-01-01T00:00:00Z",
            "urgency": "low",
            "min_client_version": 1,
            "platforms": platforms,
        }],
    }


def _entry(filename, **kw):
    return {
        "platform": kw,
        "asset": {
            "filename": filename,
            "size_bytes": 1,
            "sha256": "a" * 64,
            "urls": [f"https://example.invalid/{filename}"],
        },
    }


# --- _expand_arch -------------------------------------------------------


def test_x86_expands_to_both_archs():
    """The headline case: `x86` is genuinely ambiguous (modern desktop
    Intel vs. legacy 32-bit). Expansion gives 64-bit first."""
    assert _expand_arch("x86") == ["x86_64", "i686"]


def test_arm_expands_to_both_archs():
    assert _expand_arch("arm") == ["aarch64", "armv7"]


def test_canonical_arch_expands_to_self():
    assert _expand_arch("x86_64") == ["x86_64"]
    assert _expand_arch("aarch64") == ["aarch64"]


def test_single_alias_expands_to_one_canonical():
    assert _expand_arch("x64") == ["x86_64"]
    assert _expand_arch("arm64") == ["aarch64"]
    assert _expand_arch("amd64") == ["x86_64"]


def test_unknown_arch_passes_through_as_single():
    assert _expand_arch("nonsense") == ["nonsense"]


# --- resolve_all_in_catalog --------------------------------------------


def test_x86_returns_both_when_both_published():
    """Catalog ships both x86_64 and i686. `x86` query returns both,
    x86_64 first (preferred)."""
    cat = _catalog([
        _entry("tool-x86_64.tar.gz", os="linux", arch="x86_64", libc="glibc"),
        _entry("tool-i686.tar.gz",   os="linux", arch="i686",   libc="glibc"),
    ])
    results = resolve_all_in_catalog(
        cat, "demo",
        platform={"os": "linux", "arch": "x86", "libc": "glibc"},
        channel="latest-stable",
    )
    assert len(results) == 2
    assert results[0].asset["filename"] == "tool-x86_64.tar.gz"
    assert results[1].asset["filename"] == "tool-i686.tar.gz"


def test_x86_returns_only_existing_when_only_one_published():
    """If only i686 is published, `x86` query returns just that — even
    though x86_64 is the preferred expansion."""
    cat = _catalog([
        _entry("tool-i686.tar.gz", os="linux", arch="i686", libc="glibc"),
    ])
    results = resolve_all_in_catalog(
        cat, "demo",
        platform={"os": "linux", "arch": "x86", "libc": "glibc"},
        channel="latest-stable",
    )
    assert len(results) == 1
    assert results[0].asset["filename"] == "tool-i686.tar.gz"


def test_arm_returns_both_when_both_published():
    cat = _catalog([
        _entry("tool-aarch64.tar.gz", os="linux", arch="aarch64", libc="glibc"),
        _entry("tool-armv7.tar.gz",   os="linux", arch="armv7",   libc="glibc"),
    ])
    results = resolve_all_in_catalog(
        cat, "demo",
        platform={"os": "linux", "arch": "arm", "libc": "glibc"},
        channel="latest-stable",
    )
    assert len(results) == 2
    assert results[0].asset["filename"] == "tool-aarch64.tar.gz"


def test_canonical_arch_query_returns_single():
    cat = _catalog([
        _entry("tool-x86_64.tar.gz", os="linux", arch="x86_64", libc="glibc"),
        _entry("tool-i686.tar.gz",   os="linux", arch="i686",   libc="glibc"),
    ])
    results = resolve_all_in_catalog(
        cat, "demo",
        platform={"os": "linux", "arch": "x86_64", "libc": "glibc"},
        channel="latest-stable",
    )
    assert len(results) == 1
    assert results[0].asset["filename"] == "tool-x86_64.tar.gz"


def test_results_deduped_when_same_asset_matches_multiple_expansions():
    """An asset listed as `universal2` shouldn't appear twice in the
    output just because both expansions of `x86` lead to it via the
    universal-arch compat path."""
    cat = _catalog([
        _entry("sdk.tar.zstd", os="darwin", arch="universal2"),
    ])
    results = resolve_all_in_catalog(
        cat, "demo",
        platform={"os": "darwin", "arch": "x86"},
        channel="latest-stable",
    )
    # x86 expands to [x86_64, i686]; both can match universal2 via compat
    # — but the asset has the same filename+sha so we dedupe.
    assert len(results) == 1
    assert results[0].asset["filename"] == "sdk.tar.zstd"


def test_empty_result_on_no_match():
    cat = _catalog([_entry("only-linux.tar.gz", os="linux", arch="x86_64")])
    results = resolve_all_in_catalog(
        cat, "demo",
        platform={"os": "haiku", "arch": "x86_64"},
        channel="latest-stable",
    )
    assert results == []


def test_resolve_in_catalog_still_raises_ambiguity():
    """The single-result API is unchanged — when multiple matches tie
    at top specificity, it still raises so naive callers don't silently
    get the wrong asset."""
    cat = _catalog([
        _entry("a.tar.gz", os="linux", arch="x86_64", libc="glibc"),
        _entry("b.tar.gz", os="linux", arch="x86_64", libc="musl"),
    ])
    with pytest.raises(AmbiguityError):
        resolve_in_catalog(
            cat, "demo",
            platform={"os": "linux", "arch": "x86_64"},
            channel="latest-stable",
        )

    # ...but resolve_all returns both.
    results = resolve_all_in_catalog(
        cat, "demo",
        platform={"os": "linux", "arch": "x86_64"},
        channel="latest-stable",
    )
    assert len(results) == 2


def test_arch_priority_beats_specificity_in_ordering():
    """If x86_64 has a specificity-2 match AND i686 has a specificity-3
    match, the x86_64 result comes first because arch-expansion priority
    is the outer sort key."""
    cat = _catalog([
        _entry("x86_64-generic.tar.gz", os="linux", arch="x86_64"),
        _entry("i686-glibc.tar.gz",     os="linux", arch="i686",   libc="glibc"),
    ])
    results = resolve_all_in_catalog(
        cat, "demo",
        platform={"os": "linux", "arch": "x86", "libc": "glibc"},
        channel="latest-stable",
    )
    assert len(results) == 2
    assert results[0].asset["filename"] == "x86_64-generic.tar.gz"
    assert results[1].asset["filename"] == "i686-glibc.tar.gz"
