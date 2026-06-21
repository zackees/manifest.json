"""Resolution algorithm — DESIGN.md §5.

resolve(tool, platform, channel, variant?) -> Asset
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


class ResolveError(Exception):
    """Base class for resolution failures."""


class SchemaError(ResolveError):
    """Document is not a recognized kind or has the wrong tool."""


class ToolNotFoundError(ResolveError):
    """Index has no entry for the requested tool."""


class ChannelNotFoundError(ResolveError):
    """Catalog has no entry for the requested channel."""


class VersionNotInCatalogError(ResolveError):
    """Channel resolved to a version that does not appear in releases[]."""


class NoMatchingAssetError(ResolveError):
    """No (platform, variant) entry in the release matches the query."""


class NoBinaryOrSourceError(ResolveError):
    """No matching binary AND no source fallback available."""


class AmbiguityError(ResolveError):
    """Multiple (platform, variant) entries matched; caller must narrow."""

    def __init__(self, candidates: list[dict[str, Any]]):
        super().__init__(
            f"{len(candidates)} candidates matched; narrow the query"
        )
        self.candidates = candidates


@dataclass
class Resolution:
    """Tagged result of resolve_or_source.

    `kind == "binary"`: `asset` holds a matching prebuilt Asset.
    `kind == "source"`: `source` holds the Release's Source fallback.
    """

    kind: Literal["binary", "source"]
    asset: dict[str, Any] | None = None
    source: dict[str, Any] | None = None
    version: str = ""


def platform_matches(stored: dict[str, Any], query: dict[str, Any]) -> bool:
    """True iff `stored` and `query` agree on every field present in both.

    A field present in `query` but missing/empty in `stored` is a wildcard
    in `stored` (the producer didn't constrain it) and matches. A field
    present in `stored` but missing/empty in `query` is a wildcard in the
    query (the caller doesn't care) and matches.

    `features` is a list; the query's features must be a subset of stored's.
    """
    for key, qval in query.items():
        if not qval:
            continue
        sval = stored.get(key)
        if not sval:
            continue
        if key == "features":
            if not set(qval).issubset(set(sval)):
                return False
        elif sval != qval:
            return False
    for key, sval in stored.items():
        if not sval:
            continue
        qval = query.get(key)
        if not qval:
            continue
        if key == "features":
            if not set(qval).issubset(set(sval)):
                return False
        elif sval != qval:
            return False
    return True


def variant_matches(stored: dict[str, Any], query: dict[str, Any]) -> bool:
    """Same wildcard semantics as platform_matches, applied to variants."""
    if not query:
        return True
    for key, qval in query.items():
        if not qval:
            continue
        sval = stored.get(key)
        if not sval:
            continue
        if sval != qval:
            return False
    return True


def resolve_in_catalog(
    catalog: dict[str, Any],
    tool: str,
    platform: dict[str, Any],
    channel: str,
    variant: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a (tool, platform, channel, variant) query against a Catalog
    document. Returns the matching Asset dict.

    Raises a subclass of ResolveError on any failure.
    """
    if catalog.get("kind") != "Catalog":
        raise SchemaError(f"expected kind=Catalog, got {catalog.get('kind')!r}")
    if catalog.get("tool") != tool:
        raise SchemaError(
            f"catalog is for tool {catalog.get('tool')!r}, queried {tool!r}"
        )

    channels = catalog.get("channels", {})
    if channel not in channels:
        raise ChannelNotFoundError(
            f"channel {channel!r} not in catalog (have: {sorted(channels)})"
        )
    version = channels[channel]

    release = None
    for r in catalog.get("releases", []):
        if r.get("version") == version:
            release = r
            break
    if release is None:
        raise VersionNotInCatalogError(
            f"channel {channel!r} -> version {version!r}, but not in releases[]"
        )

    matches: list[dict[str, Any]] = []
    for rp in release.get("platforms", []):
        stored_platform = rp.get("platform", {}) or {}
        stored_variant = rp.get("variant", {}) or {}
        if platform_matches(stored_platform, platform) and variant_matches(
            stored_variant, variant or {}
        ):
            matches.append(rp)

    if not matches:
        raise NoMatchingAssetError(
            f"no asset in release {version!r} matches "
            f"platform={platform!r} variant={variant!r}"
        )
    if len(matches) > 1:
        raise AmbiguityError(matches)

    return matches[0]["asset"]


def resolve_or_source(
    catalog: dict[str, Any],
    tool: str,
    platform: dict[str, Any],
    channel: str,
    variant: dict[str, Any] | None = None,
) -> Resolution:
    """Like resolve_in_catalog, but falls back to the Release's `source`
    field when no binary matches. Returns a Resolution tagged with
    `kind="binary"` or `kind="source"`.

    Raises NoBinaryOrSourceError when neither is available. All other
    ResolveError subclasses (SchemaError, ChannelNotFoundError,
    AmbiguityError, ...) propagate unchanged — falling back to source
    on ambiguity would mask a producer bug.
    """
    if catalog.get("kind") != "Catalog":
        raise SchemaError(f"expected kind=Catalog, got {catalog.get('kind')!r}")
    if catalog.get("tool") != tool:
        raise SchemaError(
            f"catalog is for tool {catalog.get('tool')!r}, queried {tool!r}"
        )

    channels = catalog.get("channels", {})
    if channel not in channels:
        raise ChannelNotFoundError(
            f"channel {channel!r} not in catalog (have: {sorted(channels)})"
        )
    version = channels[channel]

    release = None
    for r in catalog.get("releases", []):
        if r.get("version") == version:
            release = r
            break
    if release is None:
        raise VersionNotInCatalogError(
            f"channel {channel!r} -> version {version!r}, but not in releases[]"
        )

    try:
        asset = resolve_in_catalog(catalog, tool, platform, channel, variant)
    except NoMatchingAssetError:
        source = release.get("source") or {}
        if not source:
            raise NoBinaryOrSourceError(
                f"no binary in release {version!r} matches "
                f"platform={platform!r} variant={variant!r}, "
                f"and no source fallback is declared"
            ) from None
        return Resolution(kind="source", source=source, version=version)
    return Resolution(kind="binary", asset=asset, version=version)
