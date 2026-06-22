"""manifest.json - codified format for describing downloadable artifacts."""

from manifest_json.resolve import (
    AmbiguityError,
    ChannelNotFoundError,
    NoBinaryOrSourceError,
    NoMatchingAssetError,
    Resolution,
    ResolveError,
    SchemaError,
    ToolNotFoundError,
    VersionNotInCatalogError,
    normalize_platform,
    platform_matches,
    resolve_all_in_catalog,
    resolve_in_catalog,
    resolve_or_source,
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
    "NoBinaryOrSourceError",
    "NoMatchingAssetError",
    "Resolution",
    "ResolveError",
    "SchemaError",
    "ToolNotFoundError",
    "ValidationError",
    "VersionNotInCatalogError",
    "compile_for_target",
    "generate_json_schema",
    "normalize_platform",
    "platform_matches",
    "resolve_all_in_catalog",
    "resolve_in_catalog",
    "resolve_or_source",
    "validate_catalog_semantics",
    "validate_document",
    "variant_matches",
]
