"""Query-side alias normalization for arch + OS.

Callers come from many ecosystems with different naming conventions
for the same architecture:

  - npm: `x64`, `arm64`
  - Debian / Docker: `amd64`, `arm64`
  - Rust target triples: `x86_64`, `aarch64`
  - Casual usage: `x86` (often means modern 64-bit Intel desktop)

Canonical values per DESIGN.md §3.1 are `x86_64`, `aarch64`. The
resolver normalizes the caller's query through these aliases before
matching so the same query produces the same answer regardless of
which convention the caller grew up with.

Producer-side (stored) values are NOT normalized — that would mask
producer bugs (e.g. shipping `arm64` instead of `aarch64`).
"""

from __future__ import annotations

import pytest

from manifest_json.resolve import (
    NoMatchingAssetError,
    _normalize_arch,
    _normalize_os,
    normalize_platform,
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


# --- _normalize_arch ----------------------------------------------------


@pytest.mark.parametrize("alias,canonical", [
    ("x86_64",  "x86_64"),
    ("x64",     "x86_64"),
    ("amd64",   "x86_64"),
    ("x86-64",  "x86_64"),
    ("x86",     "x86_64"),  # contentious; matches modern desktop usage
    ("aarch64", "aarch64"),
    ("arm64",   "aarch64"),
    ("arm",     "aarch64"),
    ("armv7",   "armv7"),
    ("armhf",   "armv7"),
    ("i686",    "i686"),
    ("i386",    "i686"),
    ("x86_32",  "i686"),
    ("riscv64", "riscv64"),
    ("rv64",    "riscv64"),
    ("wasm32",  "wasm32"),
    ("wasm",    "wasm32"),
    ("universal",  "universal"),
    ("universal2", "universal2"),
])
def test_arch_aliases(alias, canonical):
    assert _normalize_arch(alias) == canonical


def test_arch_alias_is_case_insensitive():
    assert _normalize_arch("X64") == "x86_64"
    assert _normalize_arch("ARM64") == "aarch64"
    assert _normalize_arch("AMD64") == "x86_64"


def test_unknown_arch_passes_through():
    """Unknown arch values aren't rewritten — they still get equality-
    compared against stored values, just opaquely."""
    assert _normalize_arch("nonsense") == "nonsense"
    assert _normalize_arch("") == ""


def test_i686_does_NOT_alias_to_x86_64():
    """32-bit Intel kept distinct from 64-bit. If you really want 32-bit
    you use i686/i386/x86_32 — they all map to i686, never x86_64."""
    assert _normalize_arch("i686") == "i686"
    assert _normalize_arch("i386") == "i686"
    assert _normalize_arch("i686") != "x86_64"


# --- _normalize_os ------------------------------------------------------


@pytest.mark.parametrize("alias,canonical", [
    ("linux",    "linux"),
    ("darwin",   "darwin"),
    ("macos",    "darwin"),
    ("mac",      "darwin"),
    ("osx",      "darwin"),
    ("macosx",   "darwin"),
    ("windows",  "windows"),
    ("win",      "windows"),
    ("win32",    "windows"),
    ("win64",    "windows"),
    ("freebsd",  "freebsd"),
    ("wasi",     "wasi"),
])
def test_os_aliases(alias, canonical):
    assert _normalize_os(alias) == canonical


def test_os_alias_is_case_insensitive():
    assert _normalize_os("Darwin") == "darwin"
    assert _normalize_os("WINDOWS") == "windows"


# --- normalize_platform (whole-tuple) -----------------------------------


def test_normalize_platform_full_tuple():
    p = normalize_platform({"os": "mac", "arch": "arm64", "libc": "glibc"})
    assert p == {"os": "darwin", "arch": "aarch64", "libc": "glibc"}


def test_normalize_platform_passes_other_fields_unchanged():
    p = normalize_platform({
        "os": "linux", "arch": "x64",
        "os_version": "11.0", "libc": "musl",
        "abi": "gnu", "features": ["avx2"],
    })
    assert p == {
        "os": "linux", "arch": "x86_64",
        "os_version": "11.0", "libc": "musl",
        "abi": "gnu", "features": ["avx2"],
    }


def test_normalize_empty_platform():
    assert normalize_platform({}) == {}


# --- resolver end-to-end ------------------------------------------------


def test_resolver_uses_aliases_for_arch():
    """A producer publishes the canonical {os:linux, arch:x86_64} entry;
    queries with any common alias resolve to it."""
    cat = _catalog([_entry("tool-x64.tar.gz", os="linux", arch="x86_64", libc="glibc")])
    for query_arch in ("x86_64", "x64", "amd64", "x86", "X64"):
        asset = resolve_in_catalog(
            cat, "demo",
            platform={"os": "linux", "arch": query_arch, "libc": "glibc"},
            channel="latest-stable",
        )
        assert asset["filename"] == "tool-x64.tar.gz", f"failed for arch={query_arch!r}"


def test_resolver_uses_aliases_for_os():
    cat = _catalog([_entry("tool-darwin.tar.gz", os="darwin", arch="aarch64")])
    for query_os in ("darwin", "macos", "mac", "osx", "Darwin"):
        asset = resolve_in_catalog(
            cat, "demo",
            platform={"os": query_os, "arch": "arm64"},  # also test arch alias
            channel="latest-stable",
        )
        assert asset["filename"] == "tool-darwin.tar.gz", f"failed for os={query_os!r}"


def test_x86_meaning_x86_64_works_with_universal2():
    """The headline use case: a consumer types `x86` (meaning modern
    desktop Intel), against a producer that ships only universal2 on
    darwin. The query normalizes to x86_64, which the universal-arch
    compat then matches against universal2."""
    cat = _catalog([_entry("sdk.tar.zstd", os="darwin", arch="universal2")])
    asset = resolve_in_catalog(
        cat, "demo",
        platform={"os": "darwin", "arch": "x86"},
        channel="latest-stable",
    )
    assert asset["filename"] == "sdk.tar.zstd"

    # And `arm` → aarch64 → universal2 also works
    asset = resolve_in_catalog(
        cat, "demo",
        platform={"os": "macos", "arch": "arm"},
        channel="latest-stable",
    )
    assert asset["filename"] == "sdk.tar.zstd"


def test_producer_side_NOT_normalized():
    """If a producer mistakenly stores arch=arm64 instead of aarch64,
    the resolver doesn't auto-fix it — a query for aarch64 should
    raise NoMatchingAsset so the bug is visible. Storing aliases is
    a producer bug; the validator/CI should catch it."""
    cat = _catalog([_entry("typo.tar.gz", os="linux", arch="arm64")])  # producer bug
    # A query that ALSO uses arm64 happens to work (normalized to
    # aarch64, but no aarch64 stored either — but it WILL match
    # because... wait, query arm64 -> aarch64; stored arm64 stays
    # arm64; arches not equal; NO match).
    with pytest.raises(NoMatchingAssetError):
        resolve_in_catalog(
            cat, "demo",
            platform={"os": "linux", "arch": "arm64"},
            channel="latest-stable",
        )


def test_npm_to_debian_to_rust_user_all_work():
    """One producer, three consumer ecosystems — all get the right asset."""
    cat = _catalog([
        _entry("linux-x86_64.tar.gz", os="linux", arch="x86_64", libc="glibc"),
        _entry("linux-aarch64.tar.gz", os="linux", arch="aarch64", libc="glibc"),
    ])
    queries = [
        ("npm",    {"os": "linux", "arch": "x64",     "libc": "glibc"}, "linux-x86_64.tar.gz"),
        ("debian", {"os": "linux", "arch": "amd64",   "libc": "glibc"}, "linux-x86_64.tar.gz"),
        ("rust",   {"os": "linux", "arch": "x86_64",  "libc": "glibc"}, "linux-x86_64.tar.gz"),
        ("npm",    {"os": "linux", "arch": "arm64",   "libc": "glibc"}, "linux-aarch64.tar.gz"),
        ("rust",   {"os": "linux", "arch": "aarch64", "libc": "glibc"}, "linux-aarch64.tar.gz"),
        ("casual", {"os": "linux", "arch": "arm",     "libc": "glibc"}, "linux-aarch64.tar.gz"),
    ]
    for ecosystem, query, expected in queries:
        asset = resolve_in_catalog(cat, "demo", platform=query, channel="latest-stable")
        assert asset["filename"] == expected, f"{ecosystem}: {query!r} -> {asset['filename']!r}"
