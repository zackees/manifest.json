"""JSON Schema generation."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from manifest_json.schema import ROOT_KINDS, generate_json_schema


def test_schema_has_all_message_defs() -> None:
    schema = generate_json_schema()
    expected = {
        "Asset",
        "Catalog",
        "CompiledFor",
        "Component",
        "Descriptor",
        "EmbeddedSlice",
        "Index",
        "Part",
        "Platform",
        "Release",
        "ReleasePlatform",
        "Signature",
        "ToolEntry",
        "Variant",
    }
    assert expected <= set(schema["$defs"].keys())


def test_schema_has_oneof_per_root_kind() -> None:
    schema = generate_json_schema()
    assert len(schema["oneOf"]) == len(ROOT_KINDS)


def test_schema_validates_with_meta() -> None:
    """The generated schema is itself a valid JSON Schema 2020-12 document."""
    schema = generate_json_schema()
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)


def test_committed_schema_matches_generated(repo_root: Path) -> None:
    """ci/gen_schema.py output must match the committed file (drift check
    enforced at CI time; this is the unit-test equivalent)."""
    committed_path = repo_root / "schema" / "manifest.schema.json"
    if not committed_path.exists():
        pytest.skip("schema/manifest.schema.json not yet generated")
    committed = json.loads(committed_path.read_text(encoding="utf-8"))
    generated = generate_json_schema()
    assert generated == committed, (
        "schema drift: run `python ci/gen_schema.py` and commit"
    )


def test_map_field_is_object_not_array() -> None:
    """Catalog.channels is map<string, string> — must project to object,
    not an array of map entries."""
    schema = generate_json_schema()
    catalog_props = schema["$defs"]["Catalog"]["properties"]
    assert catalog_props["channels"]["type"] == "object"
    assert catalog_props["channels"]["additionalProperties"] == {"type": "string"}
