"""Resolution algorithm — the load-bearing query (DESIGN.md §5)."""

from __future__ import annotations

import pytest

from manifest_json.resolve import (
    AmbiguityError,
    ChannelNotFoundError,
    NoMatchingAssetError,
    SchemaError,
    platform_matches,
    resolve_in_catalog,
    variant_matches,
)


# --- platform_matches semantics -----------------------------------------


def test_exact_match() -> None:
    assert platform_matches(
        {"os": "linux", "arch": "x86_64"},
        {"os": "linux", "arch": "x86_64"},
    )


def test_missing_in_query_is_wildcard() -> None:
    # Stored constrains libc; query doesn't care -> match.
    assert platform_matches(
        {"os": "linux", "arch": "x86_64", "libc": "glibc"},
        {"os": "linux", "arch": "x86_64"},
    )


def test_missing_in_stored_is_wildcard() -> None:
    # Stored doesn't constrain libc; query asks for musl -> still matches
    # (producer chose not to commit to one).
    assert platform_matches(
        {"os": "linux", "arch": "x86_64"},
        {"os": "linux", "arch": "x86_64", "libc": "musl"},
    )


def test_field_mismatch_is_rejected() -> None:
    assert not platform_matches(
        {"os": "linux", "arch": "x86_64", "libc": "glibc"},
        {"os": "linux", "arch": "x86_64", "libc": "musl"},
    )


def test_features_subset_passes() -> None:
    assert platform_matches(
        {"os": "linux", "arch": "x86_64", "features": ["avx2", "sse4"]},
        {"os": "linux", "arch": "x86_64", "features": ["avx2"]},
    )


def test_variant_wildcard_when_empty() -> None:
    assert variant_matches({"edition": "full"}, {})


# --- end-to-end resolve_in_catalog --------------------------------------


def test_resolve_latest_stable_linux_glibc(catalog: dict) -> None:
    asset = resolve_in_catalog(
        catalog,
        tool="clang",
        platform={"os": "linux", "arch": "x86_64", "libc": "glibc"},
        channel="latest-stable",
    )
    assert asset["filename"] == "llvm-21.1.5-linux-x86_64.tar.zst"
    assert asset["sha256"] == "4021cc49d70472122761709e7376835dfc857b5ec77183fa969b5f61d0f13a2f"


def test_resolve_latest_stable_linux_musl(catalog: dict) -> None:
    asset = resolve_in_catalog(
        catalog,
        tool="clang",
        platform={"os": "linux", "arch": "x86_64", "libc": "musl"},
        channel="latest-stable",
    )
    assert "musl" in asset["filename"]


def test_resolve_windows_msvc_vs_gnullvm(catalog: dict) -> None:
    msvc = resolve_in_catalog(
        catalog,
        tool="clang",
        platform={"os": "windows", "arch": "x86_64", "abi": "msvc"},
        channel="latest-stable",
    )
    gnu = resolve_in_catalog(
        catalog,
        tool="clang",
        platform={"os": "windows", "arch": "x86_64", "abi": "gnullvm"},
        channel="latest-stable",
    )
    assert msvc["filename"] != gnu["filename"]
    assert "mingw" in gnu["filename"]


def test_resolve_ambiguous_linux_no_libc(catalog: dict) -> None:
    # Catalog ships glibc + musl for linux/x86_64; query doesn't pick one.
    with pytest.raises(AmbiguityError) as exc_info:
        resolve_in_catalog(
            catalog,
            tool="clang",
            platform={"os": "linux", "arch": "x86_64"},
            channel="latest-stable",
        )
    assert len(exc_info.value.candidates) == 2


def test_resolve_nightly_channel(catalog: dict) -> None:
    asset = resolve_in_catalog(
        catalog,
        tool="clang",
        platform={"os": "linux", "arch": "x86_64", "libc": "glibc"},
        channel="nightly",
    )
    assert "nightly" in asset["filename"]


def test_resolve_exact_version_works_like_channel(catalog: dict) -> None:
    # Producer can add "21.1.5": "21.1.5" if they want direct version queries.
    # Here we verify that channels share the same code path.
    asset_stable = resolve_in_catalog(
        catalog,
        tool="clang",
        platform={"os": "linux", "arch": "x86_64", "libc": "glibc"},
        channel="stable",
    )
    asset_latest = resolve_in_catalog(
        catalog,
        tool="clang",
        platform={"os": "linux", "arch": "x86_64", "libc": "glibc"},
        channel="latest-stable",
    )
    assert asset_stable == asset_latest


def test_unknown_channel(catalog: dict) -> None:
    with pytest.raises(ChannelNotFoundError):
        resolve_in_catalog(
            catalog,
            tool="clang",
            platform={"os": "linux", "arch": "x86_64"},
            channel="canary",
        )


def test_unknown_platform(catalog: dict) -> None:
    with pytest.raises(NoMatchingAssetError):
        resolve_in_catalog(
            catalog,
            tool="clang",
            platform={"os": "freebsd", "arch": "x86_64"},
            channel="latest-stable",
        )


def test_wrong_tool(catalog: dict) -> None:
    with pytest.raises(SchemaError):
        resolve_in_catalog(
            catalog,
            tool="not-clang",
            platform={"os": "linux", "arch": "x86_64"},
            channel="latest-stable",
        )
