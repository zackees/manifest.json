"""manifest.json - codified format for describing downloadable artifacts."""

from manifest_json.resolve import (
    AmbiguityError,
    ChannelNotFoundError,
    NoMatchingAssetError,
    ResolveError,
    SchemaError,
    ToolNotFoundError,
    VersionNotInCatalogError,
    platform_matches,
    resolve_in_catalog,
    variant_matches,
)
from manifest_json.compile import compile_for_target
from manifest_json.schema import generate_json_schema
from manifest_json.validate import (
    ValidationError,
    validate_catalog_semantics,
    validate_document,
)

__all__ = [
    "AmbiguityError",
    "ChannelNotFoundError",
    "NoMatchingAssetError",
    "ResolveError",
    "SchemaError",
    "ToolNotFoundError",
    "ValidationError",
    "VersionNotInCatalogError",
    "compile_for_target",
    "generate_json_schema",
    "platform_matches",
    "resolve_in_catalog",
    "validate_catalog_semantics",
    "validate_document",
    "variant_matches",
]
