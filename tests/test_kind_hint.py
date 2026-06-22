"""ToolEntry.kind_hint — free-form category for SDK/sysroot/library/etc."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from manifest_json.schema import generate_json_schema
from manifest_json.validate import ValidationError, validate_document


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def index() -> dict:
    return json.loads((EXAMPLES / "index.json").read_text(encoding="utf-8"))


def test_kind_hint_is_optional(index: dict) -> None:
    """An Index that omits kind_hint on every tool must still validate —
    the field is informational, not required."""
    no_hints = copy.deepcopy(index)
    for entry in no_hints["tools"].values():
        entry.pop("kind_hint", None)
    validate_document(no_hints)


def test_kind_hint_arbitrary_string_accepted(index: dict) -> None:
    """No controlled vocabulary — any string is valid."""
    arbitrary = copy.deepcopy(index)
    arbitrary["tools"]["clang"]["kind_hint"] = "custom-category-the-validator-has-never-seen"
    validate_document(arbitrary)


def test_example_demonstrates_real_categories(index: dict) -> None:
    """The committed index.json exercises both 'tool' and 'sysroot' so
    consumers landing on the example see the intended use."""
    hints = {name: entry.get("kind_hint", "") for name, entry in index["tools"].items()}
    assert hints["apple-sdk"] == "sysroot", "apple-sdk must be tagged as a sysroot, not a tool"
    assert hints["clang"] == "tool"
    assert hints["soldr"] == "tool"


def test_schema_exposes_kind_hint_as_optional_string() -> None:
    schema = generate_json_schema()
    props = schema["$defs"]["ToolEntry"]["properties"]
    assert "kind_hint" in props
    assert props["kind_hint"] == {"type": "string"}


def test_schema_does_not_constrain_kind_hint_enum() -> None:
    """Regression guard: kind_hint must remain free-form (no `enum`
    constraint). The whole point of the field is producer-defined
    vocabulary."""
    schema = generate_json_schema()
    props = schema["$defs"]["ToolEntry"]["properties"]
    assert "enum" not in props["kind_hint"], (
        "kind_hint must stay free-form; do not add an enum constraint"
    )
