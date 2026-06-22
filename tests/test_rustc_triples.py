"""Regression: resolver behavior on rustc target triples.

Issue #7 (zackees/manifest.json) suggested query-side hardening seeded
from `rustc --print target-list`. `parse_target_triple` turns rustc
triples into v1 Platform dicts:

  - rejects load-bearing vendors (uwp, win7, unikraft, ...) — lesson 1
  - disambiguates libc vs abi by OS context (rustc "gnu" = glibc on
    Linux, MinGW ABI on Windows)
  - passes unknown arches through unchanged so the resolver fails closed
    (NoMatchingAssetError) rather than fuzzy-merging near-arches like
    `x86_64h` into `x86_64` — lesson 2

The CASES table doubles as a corpus of rustc-canonical and adversarial
inputs we want the resolver to handle correctly forever.
"""

from __future__ import annotations

import pytest

from manifest_json.resolve import (
    NoMatchingAssetError,
    TripleParseError,
    parse_target_triple,
    resolve_in_catalog,
)


# Catalog covering the canonical entries a typical Rust-ecosystem
# producer would publish. libc/abi values follow the repo convention
# (see examples/catalog.json): Linux uses libc=glibc|musl; Windows uses
# abi=msvc|gnu|gnullvm.
CATALOG = {
    "kind": "Catalog",
    "schema_version": 1,
    "tool": "demo",
    "online_url": "https://example.invalid/demo/manifest.json",
    "channels": {"stable": "1.0.0"},
    "releases": [{
        "version": "1.0.0",
        "published_at": "2026-01-01T00:00:00Z",
        "urgency": "low",
        "min_client_version": 1,
        "platforms": [
            {"platform": {"os": "linux",   "arch": "x86_64",  "libc": "glibc"}, "asset": {"filename": "linux-x86_64-glibc.tar.gz"}},
            {"platform": {"os": "linux",   "arch": "x86_64",  "libc": "musl"},  "asset": {"filename": "linux-x86_64-musl.tar.gz"}},
            {"platform": {"os": "linux",   "arch": "aarch64", "libc": "glibc"}, "asset": {"filename": "linux-aarch64-glibc.tar.gz"}},
            {"platform": {"os": "darwin",  "arch": "x86_64"},                   "asset": {"filename": "darwin-x86_64.tar.gz"}},
            {"platform": {"os": "darwin",  "arch": "aarch64"},                  "asset": {"filename": "darwin-aarch64.tar.gz"}},
            {"platform": {"os": "windows", "arch": "x86_64", "abi": "msvc"},    "asset": {"filename": "windows-x86_64-msvc.zip"}},
            {"platform": {"os": "windows", "arch": "x86_64", "abi": "gnu"},     "asset": {"filename": "windows-x86_64-mingw.zip"}},
        ],
    }],
}


# (triple, expected asset filename or None, comment).
# `None` means: the resolver should NOT serve any artifact for this
# query — either parse_target_triple rejects (vendor allowlist) or the
# resolver fails closed (unknown arch).
CASES = [
    # Canonical Rust triples → matched artifact
    ("x86_64-unknown-linux-gnu",       "linux-x86_64-glibc.tar.gz",  "Linux glibc"),
    ("aarch64-unknown-linux-gnu",      "linux-aarch64-glibc.tar.gz", "Linux ARM64 glibc"),
    ("x86_64-unknown-linux-musl",      "linux-x86_64-musl.tar.gz",   "Linux musl"),
    ("x86_64-apple-darwin",            "darwin-x86_64.tar.gz",       "Intel macOS"),
    ("aarch64-apple-darwin",           "darwin-aarch64.tar.gz",      "Apple Silicon"),
    ("x86_64-pc-windows-msvc",         "windows-x86_64-msvc.zip",    "MSVC Windows"),
    ("x86_64-pc-windows-gnu",          "windows-x86_64-mingw.zip",   "MinGW Windows"),

    # Vendor rejection (issue #7 lesson 1) — these targets exist in rustc
    # but ship different artifacts from their canonical sibling.
    ("x86_64-uwp-windows-msvc",        None, "UWP — vendor rejected"),
    ("x86_64-win7-windows-msvc",       None, "win7 — vendor rejected"),
    ("x86_64-unikraft-linux-musl",     None, "unikraft — vendor rejected"),

    # Arch near-miss (issue #7 lesson 2) — vendor is canonical so the
    # parser accepts; the unknown arch passes through and the catalog
    # has no matching entry → fail closed.
    ("x86_64h-apple-darwin",           None, "Haswell+ subset of x86_64"),
    ("aarch64_be-unknown-linux-gnu",   None, "big-endian aarch64"),
]


@pytest.mark.parametrize("triple,expected_filename,comment", CASES)
def test_rustc_triple_resolves_to(triple, expected_filename, comment):
    try:
        platform = parse_target_triple(triple)
        asset = resolve_in_catalog(CATALOG, "demo", platform, "stable")
        got = asset.get("filename")
    except (TripleParseError, NoMatchingAssetError):
        got = None
    assert got == expected_filename, (
        f"\n  triple   = {triple}"
        f"\n  comment  = {comment}"
        f"\n  expected = {expected_filename!r}"
        f"\n  got      = {got!r}"
    )


def test_parse_rejects_unknown_vendor_with_directive_message():
    with pytest.raises(TripleParseError) as exc:
        parse_target_triple("x86_64-uwp-windows-msvc")
    msg = str(exc.value)
    assert "uwp" in msg
    assert "vendor" in msg.lower()


def test_parse_rejects_too_few_segments():
    with pytest.raises(TripleParseError):
        parse_target_triple("x86_64-linux")


def test_parse_normalizes_aliases_in_arch_slot():
    # rustc wouldn't spell it `amd64`, but a Docker-shaped caller might.
    p = parse_target_triple("amd64-unknown-linux-gnu")
    assert p == {"arch": "x86_64", "os": "linux", "libc": "glibc"}
