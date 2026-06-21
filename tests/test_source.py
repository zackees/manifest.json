"""Source fallback — `resolve_or_source` and `Source` validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manifest_json.resolve import (
    AmbiguityError,
    ChannelNotFoundError,
    NoBinaryOrSourceError,
    Resolution,
    resolve_or_source,
)
from manifest_json.validate import ValidationError, validate_document


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _wrap_in_catalog(release: dict, tool: str) -> dict:
    return {
        "kind": "Catalog",
        "schema_version": 1,
        "tool": tool,
        "online_url": "https://example.invalid/catalog.json",
        "channels": {"latest-stable": release["version"]},
        "releases": [
            {
                "version": release["version"],
                "published_at": release["published_at"],
                "urgency": release.get("urgency", "low"),
                "min_client_version": release.get("min_client_version", 1),
                "platforms": release.get("platforms", []),
                "source": release.get("source", {}),
            }
        ],
    }


# --- validator ---------------------------------------------------------------


def test_source_only_release_validates() -> None:
    doc = json.loads((EXAMPLES / "source_only_release.json").read_text(encoding="utf-8"))
    validate_document(doc)


def test_source_with_binaries_validates() -> None:
    doc = json.loads((EXAMPLES / "github_release.json").read_text(encoding="utf-8"))
    validate_document(doc)
    assert doc["source"]["repo_url"] == "https://github.com/zackees/soldr"


def test_source_must_have_vcs_or_archive() -> None:
    doc = json.loads((EXAMPLES / "source_only_release.json").read_text(encoding="utf-8"))
    doc["source"] = {"build_command": "make"}
    with pytest.raises(ValidationError, match="repo_url \\+ ref"):
        validate_document(doc)


def test_source_bad_sha256_rejected() -> None:
    doc = json.loads((EXAMPLES / "source_only_release.json").read_text(encoding="utf-8"))
    doc["source"]["archive_sha256"] = "not-a-sha"
    with pytest.raises(ValidationError, match="archive_sha256"):
        validate_document(doc)


def test_source_vcs_only_no_archive_ok() -> None:
    doc = json.loads((EXAMPLES / "source_only_release.json").read_text(encoding="utf-8"))
    doc["source"].pop("archive_url")
    doc["source"].pop("archive_sha256")
    doc["source"].pop("archive_size_bytes")
    validate_document(doc)


# --- resolver fallback -------------------------------------------------------


def test_resolve_or_source_returns_binary_when_match(catalog: dict) -> None:
    result = resolve_or_source(
        catalog,
        tool="clang",
        platform={"os": "linux", "arch": "x86_64", "libc": "glibc"},
        channel="latest-stable",
    )
    assert isinstance(result, Resolution)
    assert result.kind == "binary"
    assert result.asset is not None
    assert result.source is None
    assert result.version == "21.1.5"


def test_resolve_or_source_falls_back_to_source() -> None:
    release = json.loads((EXAMPLES / "source_only_release.json").read_text(encoding="utf-8"))
    catalog = _wrap_in_catalog(release, "exotic-tool")
    result = resolve_or_source(
        catalog,
        tool="exotic-tool",
        platform={"os": "linux", "arch": "x86_64"},
        channel="latest-stable",
    )
    assert result.kind == "source"
    assert result.asset is None
    assert result.source["repo_url"] == "https://github.com/example/exotic-tool"
    assert result.version == "0.3.0"


def test_resolve_or_source_raises_when_neither(catalog: dict) -> None:
    # clang catalog has binaries but no source — querying an unknown
    # platform must raise NoBinaryOrSourceError, not fall back silently.
    with pytest.raises(NoBinaryOrSourceError):
        resolve_or_source(
            catalog,
            tool="clang",
            platform={"os": "haiku", "arch": "x86_64"},
            channel="latest-stable",
        )


def test_resolve_or_source_does_not_swallow_ambiguity(catalog: dict) -> None:
    # Don't mask producer bugs by falling back to source on ambiguity.
    with pytest.raises(AmbiguityError):
        resolve_or_source(
            catalog,
            tool="clang",
            platform={"os": "linux", "arch": "x86_64"},
            channel="latest-stable",
        )


def test_resolve_or_source_propagates_channel_error(catalog: dict) -> None:
    with pytest.raises(ChannelNotFoundError):
        resolve_or_source(
            catalog,
            tool="clang",
            platform={"os": "linux", "arch": "x86_64", "libc": "glibc"},
            channel="canary",
        )


def test_binary_release_with_source_fallback_works() -> None:
    """When BOTH binaries and source are present, binary wins for matching
    platforms; source is the fallback for unmatched ones."""
    release = json.loads((EXAMPLES / "github_release.json").read_text(encoding="utf-8"))
    catalog = _wrap_in_catalog(release, "soldr")
    catalog["releases"][0]["source"] = release["source"]

    # Matching platform -> binary
    r1 = resolve_or_source(
        catalog,
        tool="soldr",
        platform={"os": "linux", "arch": "x86_64", "libc": "glibc"},
        channel="latest-stable",
    )
    assert r1.kind == "binary"

    # Non-matching platform -> source fallback
    r2 = resolve_or_source(
        catalog,
        tool="soldr",
        platform={"os": "linux", "arch": "riscv64"},
        channel="latest-stable",
    )
    assert r2.kind == "source"
    assert r2.source["build_command"] == "cargo build --release"
