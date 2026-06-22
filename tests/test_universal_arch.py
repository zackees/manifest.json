"""Universal-arch (universal2 / universal) compatibility on darwin.

Producers commonly ship a fat Mach-O that contains both x86_64 and
aarch64 code. The schema lets them declare it once with
`arch: "universal2"` instead of duplicating the same asset under two
concrete-arch entries. The resolver treats stored universal-arch
entries as compatible with concrete-arch queries.

Specificity-priority still applies: an explicit-arch entry beats a
universal entry when both are present.

Real-world trigger: zackees/soldr-toolchain's apple-sdk shipped the
same asset under both `darwin/x86_64` and `darwin/aarch64` — wasteful
and confusing.
"""

from __future__ import annotations

import pytest

from manifest_json.resolve import (
    AmbiguityError,
    _arches_compatible,
    platform_matches,
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


# --- _arches_compatible --------------------------------------------------


def test_exact_arch_match():
    assert _arches_compatible("darwin", "x86_64", "x86_64")
    assert _arches_compatible("linux",  "x86_64", "x86_64")


def test_universal2_matches_x86_64_on_darwin():
    assert _arches_compatible("darwin", "universal2", "x86_64")


def test_universal2_matches_aarch64_on_darwin():
    assert _arches_compatible("darwin", "universal2", "aarch64")


def test_universal_matches_concrete_darwin():
    assert _arches_compatible("darwin", "universal", "x86_64")
    assert _arches_compatible("darwin", "universal", "aarch64")


def test_universal_does_not_apply_on_linux():
    """Linux has no fat-binary convention; universal-arch makes no sense
    there. A producer who somehow stores it must match exactly."""
    assert not _arches_compatible("linux", "universal2", "x86_64")


def test_concrete_arch_does_not_match_universal_query():
    """The compat is one-way: a fat binary satisfies a concrete-arch
    query, but a pure x86_64 binary does NOT satisfy a query asking
    for a fat binary."""
    assert not _arches_compatible("darwin", "x86_64", "universal2")


def test_arch_mismatch_still_rejected_on_darwin():
    """Universal-arch compat doesn't make every arch match every other
    arch on darwin. A pure x86_64 entry must not be served to an aarch64
    query."""
    assert not _arches_compatible("darwin", "x86_64", "aarch64")


# --- platform_matches end-to-end ----------------------------------------


def test_resolve_universal2_when_no_explicit_variant():
    """Producer publishes a fat binary only. x86_64 + aarch64 queries
    both resolve to it."""
    cat = _catalog([_entry("sdk.tar.zstd", os="darwin", arch="universal2")])
    for query_arch in ("x86_64", "aarch64", "universal2"):
        asset = resolve_in_catalog(
            cat, "demo",
            platform={"os": "darwin", "arch": query_arch},
            channel="latest-stable",
        )
        assert asset["filename"] == "sdk.tar.zstd"


def test_explicit_arch_wins_over_universal2():
    """When both an x86_64-specific and a universal2 entry exist, an
    x86_64 query picks the explicit one (specificity-priority)."""
    cat = _catalog([
        _entry("sdk-x86_64.tar.zstd",    os="darwin", arch="x86_64"),
        _entry("sdk-universal.tar.zstd", os="darwin", arch="universal2"),
    ])
    asset = resolve_in_catalog(
        cat, "demo",
        platform={"os": "darwin", "arch": "x86_64"},
        channel="latest-stable",
    )
    assert asset["filename"] == "sdk-x86_64.tar.zstd"


def test_aarch64_query_picks_aarch64_over_universal2():
    cat = _catalog([
        _entry("sdk-aarch64.tar.zstd",   os="darwin", arch="aarch64"),
        _entry("sdk-universal.tar.zstd", os="darwin", arch="universal2"),
    ])
    asset = resolve_in_catalog(
        cat, "demo",
        platform={"os": "darwin", "arch": "aarch64"},
        channel="latest-stable",
    )
    assert asset["filename"] == "sdk-aarch64.tar.zstd"


def test_explicit_universal2_query_picks_universal2_entry():
    cat = _catalog([
        _entry("sdk-x86_64.tar.zstd",    os="darwin", arch="x86_64"),
        _entry("sdk-universal.tar.zstd", os="darwin", arch="universal2"),
    ])
    asset = resolve_in_catalog(
        cat, "demo",
        platform={"os": "darwin", "arch": "universal2"},
        channel="latest-stable",
    )
    assert asset["filename"] == "sdk-universal.tar.zstd"


def test_apple_sdk_real_world_scenario():
    """The exact pattern the soldr-toolchain audit caught: collapse two
    same-sha entries into a single universal2 entry."""
    cat = _catalog([
        _entry("sdk.tar.zstd", os="darwin", arch="universal2"),
    ])
    for arch in ("x86_64", "aarch64", "universal2"):
        asset = resolve_in_catalog(
            cat, "demo",
            platform={"os": "darwin", "arch": arch},
            channel="latest-stable",
        )
        assert asset["filename"] == "sdk.tar.zstd", f"failed for arch={arch}"


def test_apple_sdk_arm_aliased_query_resolves_via_universal2():
    """End-to-end regression guard requested mid-loop: a user-facing
    `apple-sdk` query with the very casual `arch="arm"` (and `os="mac"`)
    must resolve to the universal2 asset. The chain is:

        os    "mac"  -> normalize -> "darwin"
        arch  "arm"  -> normalize -> "aarch64"
        match darwin/aarch64 against stored darwin/universal2 via
        _arches_compatible -> True (universal-on-darwin compat)

    This mirrors the live zackees.github.io/soldr-toolchain/apple-sdk
    catalog after the universal2 collapse.
    """
    from manifest_json.resolve import resolve_all_in_catalog

    apple_sdk_catalog = {
        "kind": "Catalog",
        "schema_version": 1,
        "tool": "apple-sdk",
        "online_url": "https://zackees.github.io/soldr-toolchain/apple-sdk/manifest.json",
        "channels": {"latest-stable": "MacOSX11.3", "stable": "MacOSX11.3"},
        "releases": [{
            "schema_version": 1,
            "version": "MacOSX11.3",
            "min_client_version": 1,
            "platforms": [{
                "platform": {"os": "darwin", "arch": "universal2"},
                "asset": {
                    "filename": "sdk.tar.zstd",
                    "media_type": "application/zstd",
                    "size_bytes": 52644445,
                    "sha256": "053ac5617f5e6afd5218bec4e871cc55a6a9ab2c0b1f2f77e336dbdd48eabe56",
                    "urls": [
                        "https://media.githubusercontent.com/media/zackees/soldr-toolchain/assets/apple-sdk/MacOSX11.3/darwin-universal2/sdk.tar.zstd",
                    ],
                },
            }],
        }],
    }

    # Single-result: every alias permutation hits the same fat-binary asset.
    queries = [
        {"os": "darwin",  "arch": "arm"},
        {"os": "darwin",  "arch": "aarch64"},
        {"os": "darwin",  "arch": "arm64"},
        {"os": "darwin",  "arch": "x86_64"},
        {"os": "darwin",  "arch": "x86"},      # contentious; should still hit
        {"os": "darwin",  "arch": "x64"},
        {"os": "darwin",  "arch": "amd64"},
        {"os": "darwin",  "arch": "universal2"},
        {"os": "mac",     "arch": "arm"},
        {"os": "macos",   "arch": "arm64"},
        {"os": "osx",     "arch": "aarch64"},
    ]
    for q in queries:
        asset = resolve_in_catalog(
            apple_sdk_catalog, "apple-sdk", q, "latest-stable",
        )
        assert asset["filename"] == "sdk.tar.zstd", (
            f"single-result resolve failed for {q!r}: got {asset['filename']!r}"
        )

    # Multi-result: same queries surface exactly one match (deduped).
    for q in queries:
        results = resolve_all_in_catalog(
            apple_sdk_catalog, "apple-sdk", q, "latest-stable",
        )
        assert len(results) == 1, (
            f"resolve_all returned {len(results)} for {q!r}, want 1"
        )
        assert results[0].asset["filename"] == "sdk.tar.zstd"


def test_universal2_does_not_match_non_darwin_query():
    """A `darwin/universal2` entry must not be served to a linux query.
    The universal compat only relaxes the arch check; the os check
    still applies."""
    cat = _catalog([_entry("sdk.tar.zstd", os="darwin", arch="universal2")])
    from manifest_json.resolve import NoMatchingAssetError
    with pytest.raises(NoMatchingAssetError):
        resolve_in_catalog(
            cat, "demo",
            platform={"os": "linux", "arch": "x86_64"},
            channel="latest-stable",
        )
