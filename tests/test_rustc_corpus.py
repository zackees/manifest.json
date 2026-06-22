"""Corpus regression: every rustc target triple either parses or rejects.

Per issue #7 lesson 4 ("write the corpus test first"), this test runs the
full output of `rustc --print target-list` through `parse_target_triple`
and asserts the load-bearing invariant:

    Every line in the corpus produces exactly one of two outcomes —
    a valid v1 Platform dict, or a TripleParseError with a directive
    message. No third option (no KeyError, IndexError, garbage tuple).

The corpus file is a snapshot at `tests/corpus/rustc-target-list.txt`,
regenerated via:

    soldr rustc --print target-list > tests/corpus/rustc-target-list.txt

Snapshot version: rustc 1.94.1 (308 entries).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manifest_json.resolve import (
    TripleParseError,
    parse_target_triple,
)


CORPUS = Path(__file__).parent / "corpus" / "rustc-target-list.txt"


def _load_corpus() -> list[str]:
    return [
        line.strip()
        for line in CORPUS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_corpus_file_present_and_nontrivial():
    assert CORPUS.exists(), f"missing corpus file: {CORPUS}"
    lines = _load_corpus()
    # Snapshot was 308 entries; allow growth but flag accidental truncation.
    assert len(lines) >= 300, f"corpus shrank suspiciously: {len(lines)} lines"


@pytest.mark.parametrize("triple", _load_corpus())
def test_every_rustc_triple_parses_or_rejects_cleanly(triple):
    """No third option — parses to a valid Platform OR raises TripleParseError."""
    try:
        platform = parse_target_triple(triple)
    except TripleParseError as e:
        # Directive message must name what was rejected (issue #7 lesson 5).
        msg = str(e)
        parts = triple.split("-")
        # Vendor rejections (the common case) should name the vendor.
        if len(parts) >= 3 and parts[1] not in {"unknown", "pc", "apple"}:
            assert parts[1] in msg, (
                f"TripleParseError for {triple!r} should name the rejected "
                f"vendor {parts[1]!r} in its message; got: {msg!r}"
            )
        return
    # Parsed — must have non-empty os and arch.
    assert isinstance(platform, dict), f"{triple}: parser returned {type(platform).__name__}, not dict"
    assert platform.get("arch"), f"{triple}: parsed platform has empty arch: {platform!r}"
    assert platform.get("os"),   f"{triple}: parsed platform has empty os: {platform!r}"


def test_corpus_split_matches_expected_shape():
    """At least 200 canonical-vendor entries parse, at least 50 reject.

    Loose bounds — the point is to catch a regression where the vendor
    allowlist gets accidentally widened to "anything" (turning every
    rejection into a parse) or narrowed to nothing.
    """
    parsed = 0
    rejected = 0
    for triple in _load_corpus():
        try:
            parse_target_triple(triple)
            parsed += 1
        except TripleParseError:
            rejected += 1
    assert parsed >= 200, f"too few canonical-vendor parses: {parsed}"
    assert rejected >= 50, f"too few rejections: {rejected}"


# Sanity checks against specific entries that should never change behavior.
_SANITY_PARSES = [
    ("x86_64-unknown-linux-gnu",  {"os": "linux",   "arch": "x86_64",  "libc": "glibc"}),
    ("aarch64-apple-darwin",      {"os": "darwin",  "arch": "aarch64"}),
    ("x86_64-pc-windows-msvc",    {"os": "windows", "arch": "x86_64",  "abi": "msvc"}),
    ("aarch64-pc-windows-gnullvm",{"os": "windows", "arch": "aarch64", "abi": "gnullvm"}),
]


@pytest.mark.parametrize("triple,expected", _SANITY_PARSES)
def test_canonical_corpus_entries_parse_exactly(triple, expected):
    assert parse_target_triple(triple) == expected


_SANITY_REJECTS = [
    "x86_64-uwp-windows-msvc",      # vendor=uwp (issue #7 lesson 1)
    "x86_64-win7-windows-msvc",     # vendor=win7
    "x86_64-unikraft-linux-musl",   # vendor=unikraft
    "aarch64-nintendo-switch-freestanding",  # vendor=nintendo
    "aarch64-linux-android",        # vendor=linux (non-canonical shape)
]


@pytest.mark.parametrize("triple", _SANITY_REJECTS)
def test_load_bearing_vendors_get_rejected(triple):
    with pytest.raises(TripleParseError):
        parse_target_triple(triple)
