"""$schema field — IDE-friendly self-describing pointer."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from manifest_json.schema import generate_json_schema
from manifest_json.validate import ValidationError, validate_document


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
CANONICAL = "https://zackees.github.io/manifest.json/v1/manifest.schema.json"


def test_every_example_carries_dollar_schema() -> None:
    """Every example MUST advertise the canonical $schema URL so the
    living examples double as IDE-validation seeds."""
    for path in sorted(EXAMPLES.rglob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc.get("$schema") == CANONICAL, (
            f"{path.relative_to(EXAMPLES)}: $schema missing or wrong "
            f"({doc.get('$schema')!r})"
        )


def test_schema_field_is_in_root_schema_defs() -> None:
    """The JSON Schema must expose `$schema` (not the proto identifier
    `schema`) on every root document type."""
    schema = generate_json_schema()
    for root in ("Index", "Catalog", "Release", "EmbeddedSlice", "ArchiveContents"):
        props = schema["$defs"][root]["properties"]
        assert "$schema" in props, f"{root} missing $schema property"
        assert "schema" not in props, (
            f"{root} leaks the proto identifier `schema` — json_name annotation not honored"
        )


def test_schema_url_optional(catalog: dict) -> None:
    """A document without `$schema` must still validate."""
    no_schema = copy.deepcopy(catalog)
    no_schema.pop("$schema", None)
    validate_document(no_schema)


def test_schema_url_version_must_match_schema_version(catalog: dict) -> None:
    """If $schema URL embeds /vN/, N MUST equal schema_version."""
    bad = copy.deepcopy(catalog)
    bad["$schema"] = "https://zackees.github.io/manifest.json/v2/manifest.schema.json"
    # schema_version is still 1
    with pytest.raises(ValidationError, match=r"implies v2 but schema_version=1"):
        validate_document(bad)


def test_schema_url_unknown_pattern_tolerated(catalog: dict) -> None:
    """A custom mirror URL that doesn't match the canonical pattern is
    accepted — we can't enforce versioning on URLs we don't control."""
    ok = copy.deepcopy(catalog)
    ok["$schema"] = "https://internal-mirror.corp/our-manifest-fork.json"
    validate_document(ok)


def test_schema_url_with_query_string_still_extracts_version(catalog: dict) -> None:
    """Cache-busting query strings on the canonical URL shouldn't break
    the cross-check."""
    ok = copy.deepcopy(catalog)
    ok["$schema"] = "https://zackees.github.io/manifest.json/v1/manifest.schema.json?v=2026-06-22"
    validate_document(ok)


def test_schema_url_present_on_all_root_kinds() -> None:
    """All five root document types accept and round-trip $schema."""
    for example in (
        "index.json",
        "catalog.json",
        "github_release.json",
        "embedded_slice.json",
        "contents_manifest.json",
    ):
        doc = json.loads((EXAMPLES / example).read_text(encoding="utf-8"))
        assert doc.get("$schema") == CANONICAL, example
        validate_document(doc)
