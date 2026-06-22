"""Generate JSON Schema (Draft 2020-12) from the compiled proto descriptors.

Walks the FileDescriptor for manifest.v1 and emits a single schema document
with `$defs` for every message plus a top-level `oneOf` discriminated by the
`kind` field for root documents.
"""

from __future__ import annotations

from typing import Any

from google.protobuf.descriptor import Descriptor, FieldDescriptor

from manifest_json.proto import manifest_pb2

ROOT_KINDS: dict[str, str] = {
    "Index": "Index",
    "Catalog": "Catalog",
    "Release": "Release",
    "EmbeddedSlice": "EmbeddedSlice",
    "ArchiveContents": "ArchiveContents",
}

_SCALAR_MAP: dict[int, dict[str, Any]] = {
    FieldDescriptor.TYPE_STRING:  {"type": "string"},
    FieldDescriptor.TYPE_BOOL:    {"type": "boolean"},
    FieldDescriptor.TYPE_INT32:   {"type": "integer"},
    FieldDescriptor.TYPE_INT64:   {"type": "integer"},
    FieldDescriptor.TYPE_UINT32:  {"type": "integer", "minimum": 0},
    FieldDescriptor.TYPE_UINT64:  {"type": "integer", "minimum": 0},
    FieldDescriptor.TYPE_SINT32:  {"type": "integer"},
    FieldDescriptor.TYPE_SINT64:  {"type": "integer"},
    FieldDescriptor.TYPE_DOUBLE:  {"type": "number"},
    FieldDescriptor.TYPE_FLOAT:   {"type": "number"},
    FieldDescriptor.TYPE_BYTES:   {"type": "string", "contentEncoding": "base64"},
}


def _field_schema(field: FieldDescriptor) -> dict[str, Any]:
    if field.type == FieldDescriptor.TYPE_MESSAGE:
        msg_name = field.message_type.name
        # proto map<K, V> is exposed as a repeated message with an entry type
        # named like FooEntry with `key` (field 1) and `value` (field 2).
        if field.message_type.GetOptions().map_entry:
            value_field = field.message_type.fields_by_name["value"]
            return {
                "type": "object",
                "additionalProperties": _field_schema(value_field),
            }
        return {"$ref": f"#/$defs/{msg_name}"}
    return dict(_SCALAR_MAP[field.type])


def _message_schema(msg: Descriptor) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for field in msg.fields:
        # map<K,V> case (repeated message with map_entry=True) is handled
        # inside _field_schema and must NOT also be wrapped in an array.
        if (
            field.is_repeated
            and field.type == FieldDescriptor.TYPE_MESSAGE
            and field.message_type.GetOptions().map_entry
        ):
            properties[field.name] = _field_schema(field)
            continue
        if field.is_repeated:
            properties[field.name] = {
                "type": "array",
                "items": _field_schema(field),
            }
        else:
            properties[field.name] = _field_schema(field)
    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }


def generate_json_schema() -> dict[str, Any]:
    """Build a single JSON Schema 2020-12 document covering every root
    document type in manifest.v1."""
    file_descriptor = manifest_pb2.DESCRIPTOR
    defs: dict[str, Any] = {}
    for msg_name in file_descriptor.message_types_by_name:
        msg = file_descriptor.message_types_by_name[msg_name]
        defs[msg_name] = _message_schema(msg)

    # Add a discriminator on `kind` for each root document so a validator
    # can pick the right branch from a single schema file.
    one_of = []
    for kind, msg_name in ROOT_KINDS.items():
        branch = {
            "allOf": [
                {"$ref": f"#/$defs/{msg_name}"},
                {
                    "type": "object",
                    "properties": {"kind": {"const": kind}},
                    "required": ["kind"],
                },
            ]
        }
        one_of.append(branch)

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/zackees/manifest.json/blob/main/manifest.schema.json",
        "title": "manifest.v1",
        "description": (
            "Unified manifest.json schema. Generated from manifest.proto. "
            "Do not edit by hand."
        ),
        "$defs": defs,
        "oneOf": one_of,
    }


__all__ = ["generate_json_schema", "ROOT_KINDS"]
