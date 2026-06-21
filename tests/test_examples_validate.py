"""Every example under examples/ must structurally + semantically validate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manifest_json.validate import ValidationError, validate_document


def _example_files() -> list[Path]:
    root = Path(__file__).resolve().parents[1] / "examples"
    return sorted(root.rglob("*.json"))


@pytest.mark.parametrize("path", _example_files(), ids=lambda p: str(p.relative_to(p.parents[1])))
def test_example_validates(path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    validate_document(doc)


def test_validator_rejects_bad_sha256(catalog: dict) -> None:
    bad = json.loads(json.dumps(catalog))  # deep copy
    bad["releases"][1]["platforms"][0]["asset"]["sha256"] = "not-a-sha"
    with pytest.raises(ValidationError, match="sha256"):
        validate_document(bad)


def test_validator_rejects_dangling_channel(catalog: dict) -> None:
    bad = json.loads(json.dumps(catalog))
    bad["channels"]["latest-stable"] = "9.9.9-does-not-exist"
    with pytest.raises(ValidationError, match="no release with that version"):
        validate_document(bad)


def test_validator_rejects_duplicate_platform_in_release(catalog: dict) -> None:
    bad = json.loads(json.dumps(catalog))
    dup = json.loads(json.dumps(bad["releases"][1]["platforms"][0]))
    bad["releases"][1]["platforms"].append(dup)
    with pytest.raises(ValidationError, match="duplicate"):
        validate_document(bad)


def test_validator_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        validate_document({"kind": "Bogus", "schema_version": 1})


def test_index_requires_url_on_descriptor(index: dict) -> None:
    bad = json.loads(json.dumps(index))
    bad["tools"]["clang"]["descriptor"]["url"] = ""
    with pytest.raises(ValidationError, match="descriptor.url is required"):
        validate_document(bad)
