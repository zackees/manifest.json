"""flatten_platform: v1 Platform tuple -> canonical flat string for paths."""

from __future__ import annotations

import pytest

from manifest_json import flatten_platform


@pytest.mark.parametrize("platform,expected", [
    ({"os": "darwin",  "arch": "universal2"},                          "darwin-universal2"),
    ({"os": "darwin",  "arch": "aarch64"},                             "darwin-aarch64"),
    ({"os": "darwin",  "arch": "x86_64"},                              "darwin-x86_64"),
    ({"os": "linux",   "arch": "x86_64", "libc": "musl"},              "linux-x86_64-musl"),
    ({"os": "linux",   "arch": "x86_64", "libc": "glibc"},             "linux-x86_64-glibc"),
    ({"os": "linux",   "arch": "aarch64", "libc": "musl"},             "linux-aarch64-musl"),
    ({"os": "windows", "arch": "x86_64",  "abi":  "msvc"},             "windows-x86_64-msvc"),
    ({"os": "windows", "arch": "x86_64",  "abi":  "gnullvm"},          "windows-x86_64-gnullvm"),
    ({"os": "windows", "arch": "aarch64", "abi":  "msvc"},             "windows-aarch64-msvc"),
])
def test_canonical_cases(platform, expected):
    assert flatten_platform(platform) == expected


def test_libc_wins_when_both_libc_and_abi_present():
    """If somehow both libc and abi are set, libc takes precedence —
    libc is OS-level (durable), abi is compiler-level (more fluid)."""
    out = flatten_platform({"os": "linux", "arch": "x86_64",
                            "libc": "musl", "abi": "gnu"})
    assert out == "linux-x86_64-musl"


def test_lowercase_normalization():
    out = flatten_platform({"os": "Darwin", "arch": "Universal2"})
    assert out == "darwin-universal2"


def test_missing_os_raises():
    with pytest.raises(ValueError, match="missing 'os'"):
        flatten_platform({"arch": "x86_64"})


def test_missing_arch_raises():
    with pytest.raises(ValueError, match="missing 'arch'"):
        flatten_platform({"os": "linux"})


def test_empty_platform_raises():
    with pytest.raises(ValueError, match="empty platform"):
        flatten_platform({})


def test_extra_fields_ignored():
    """os_version, features, etc. don't affect the path — they're
    filtered out of the flat form deliberately, since the filesystem
    can't carry list-valued features cleanly anyway."""
    out = flatten_platform({
        "os": "darwin", "arch": "aarch64",
        "os_version": "11.0", "features": ["neon"],
    })
    assert out == "darwin-aarch64"


def test_roundtrip_with_real_asset_paths():
    """Sanity-check against the actual soldr-toolchain on-disk paths."""
    # apple-sdk (post universal2 collapse)
    assert flatten_platform({"os": "darwin", "arch": "universal2"}) == "darwin-universal2"
    # generic rust tools on linux musl
    assert flatten_platform({"os": "linux", "arch": "x86_64", "libc": "musl"}) == "linux-x86_64-musl"
    # rust tools on windows MSVC
    assert flatten_platform({"os": "windows", "arch": "x86_64", "abi": "msvc"}) == "windows-x86_64-msvc"
