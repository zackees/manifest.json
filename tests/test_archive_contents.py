"""ArchiveContents: full per-archive file listing as a separate document."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from manifest_json.schema import ROOT_KINDS, generate_json_schema
from manifest_json.validate import ValidationError, validate_document


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def archive_contents() -> dict:
    return json.loads((EXAMPLES / "contents_manifest.json").read_text(encoding="utf-8"))


# --- validator ---------------------------------------------------------------


def test_archive_contents_validates(archive_contents: dict) -> None:
    validate_document(archive_contents)


def test_back_link_sha256_must_be_hex(archive_contents: dict) -> None:
    bad = copy.deepcopy(archive_contents)
    bad["asset_sha256"] = "not-a-sha"
    with pytest.raises(ValidationError, match="asset_sha256"):
        validate_document(bad)


def test_file_count_must_match_files_length(archive_contents: dict) -> None:
    bad = copy.deepcopy(archive_contents)
    bad["file_count"] = 999
    with pytest.raises(ValidationError, match="file_count=999"):
        validate_document(bad)


def test_symlink_requires_linkname(archive_contents: dict) -> None:
    bad = copy.deepcopy(archive_contents)
    bad["files"].append({"path": "broken-link", "type": "symlink", "size": 0, "mode": 511})
    with pytest.raises(ValidationError, match="symlink requires `linkname`"):
        validate_document(bad)


def test_invalid_file_type_rejected(archive_contents: dict) -> None:
    bad = copy.deepcopy(archive_contents)
    bad["files"].append({"path": "bogus", "type": "device", "size": 0, "mode": 493})
    bad["file_count"] += 1
    with pytest.raises(ValidationError, match="type must be one of"):
        validate_document(bad)


def test_backslash_in_path_rejected(archive_contents: dict) -> None:
    bad = copy.deepcopy(archive_contents)
    bad["files"].append({"path": "bin\\windows-style", "type": "file", "size": 1, "mode": 493})
    bad["file_count"] += 1
    with pytest.raises(ValidationError, match="forward slashes"):
        validate_document(bad)


def test_duplicate_paths_rejected(archive_contents: dict) -> None:
    bad = copy.deepcopy(archive_contents)
    bad["files"].append(copy.deepcopy(bad["files"][1]))
    bad["file_count"] += 1
    with pytest.raises(ValidationError, match="duplicate path"):
        validate_document(bad)


# --- schema integration ------------------------------------------------------


def test_archive_contents_in_root_kinds() -> None:
    assert "ArchiveContents" in ROOT_KINDS


def test_schema_has_archive_contents_def() -> None:
    schema = generate_json_schema()
    assert "ArchiveContents" in schema["$defs"]
    assert "ArchiveFile" in schema["$defs"]


# --- Asset.provides[] and contents_manifest ----------------------------------


def test_catalog_asset_carries_provides(catalog: dict) -> None:
    asset = catalog["releases"][1]["platforms"][0]["asset"]
    assert "clang-tidy" in asset["provides"]
    assert "clang-format" in asset["provides"]


def test_catalog_asset_carries_contents_manifest_pointer(catalog: dict) -> None:
    asset = catalog["releases"][1]["platforms"][0]["asset"]
    desc = asset["contents_manifest"]
    assert desc["url"].endswith("contents_manifest.json")
    assert desc["media_type"] == "application/vnd.manifest.v1+json"


def test_provides_grep_use_case(catalog: dict) -> None:
    """The headline use case for provides[]: 'which archives ship clang-tidy?'
    answered without fetching the archives themselves."""
    hits = [
        rp
        for r in catalog["releases"]
        for rp in r["platforms"]
        if "clang-tidy" in (rp["asset"].get("provides") or [])
    ]
    assert len(hits) >= 1
