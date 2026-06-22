"""Sanity-check the compiled proto bindings."""

from __future__ import annotations

from google.protobuf.descriptor import FieldDescriptor

from manifest_json.proto import manifest_pb2


def test_all_messages_present() -> None:
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
    actual = set(manifest_pb2.DESCRIPTOR.message_types_by_name.keys())
    assert expected <= actual, f"missing messages: {expected - actual}"


def test_catalog_field_numbers_stable() -> None:
    """Field numbers are forward-compat critical (DESIGN.md §9). Lock them
    so a future refactor can't silently break wire compat."""
    catalog = manifest_pb2.DESCRIPTOR.message_types_by_name["Catalog"]
    by_name = {f.name: f.number for f in catalog.fields}
    assert by_name == {
        "kind": 1,
        "schema_version": 2,
        "tool": 3,
        "online_url": 4,
        "channels": 5,
        "releases": 6,
        "schema": 7,
    }


def test_tool_entry_field_numbers_stable() -> None:
    te = manifest_pb2.DESCRIPTOR.message_types_by_name["ToolEntry"]
    by_name = {f.name: f.number for f in te.fields}
    assert by_name == {
        "descriptor": 1,
        "summary": 2,
        "kind_hint": 3,
    }


def test_root_documents_carry_schema_field_with_dollar_json_name() -> None:
    """All five root document types expose an optional `schema` field
    that projects to `$schema` on the JSON wire."""
    for msg_name in ("Index", "Catalog", "Release", "EmbeddedSlice", "ArchiveContents"):
        msg = manifest_pb2.DESCRIPTOR.message_types_by_name[msg_name]
        field = msg.fields_by_name.get("schema")
        assert field is not None, f"{msg_name} missing `schema` field"
        assert field.type == FieldDescriptor.TYPE_STRING
        assert field.json_name == "$schema", (
            f"{msg_name}.schema json_name = {field.json_name!r}, want '$schema'"
        )


def test_platform_field_numbers_stable() -> None:
    platform = manifest_pb2.DESCRIPTOR.message_types_by_name["Platform"]
    by_name = {f.name: f.number for f in platform.fields}
    assert by_name == {
        "os": 1,
        "arch": 2,
        "os_version": 3,
        "libc": 4,
        "abi": 5,
        "features": 6,
    }


def test_asset_field_numbers_stable() -> None:
    asset = manifest_pb2.DESCRIPTOR.message_types_by_name["Asset"]
    by_name = {f.name: f.number for f in asset.fields}
    assert by_name == {
        "filename": 1,
        "media_type": 2,
        "size_bytes": 3,
        "sha256": 4,
        "urls": 5,
        "parts": 6,
        "signatures": 7,
        "sbom_url": 8,
        "provenance_url": 9,
        "yanked": 10,
        "yanked_reason": 11,
        "provides": 12,
        "contents_manifest": 13,
    }


def test_proto_message_roundtrip() -> None:
    """Build a Catalog in code; serialize; parse; verify equal."""
    cat = manifest_pb2.Catalog(
        kind="Catalog",
        schema_version=1,
        tool="clang",
        channels={"latest-stable": "21.1.5"},
    )
    cat.releases.add(version="21.1.5", published_at="2026-05-12T00:00:00Z")
    raw = cat.SerializeToString()
    other = manifest_pb2.Catalog()
    other.ParseFromString(raw)
    assert other == cat
