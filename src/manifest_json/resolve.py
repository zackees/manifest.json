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


# Multi-arch / fat-binary support. A producer can publish an asset with
# `arch: "universal2"` (Apple's term for a fat Mach-O containing both
# x86_64 and aarch64) or `arch: "universal"` (generic). The resolver
# treats those stored values as compatible with any of the concrete
# query arches listed below — so a caller running on darwin/x86_64
# gets the fat binary when no x86_64-specific variant is published.
#
# Specificity (see _specificity) ensures an explicit-arch match still
# wins when both are present.
_UNIVERSAL_DARWIN_ARCHES = {"universal", "universal2"}
_UNIVERSAL_DARWIN_COVERS = {"x86_64", "aarch64", "universal", "universal2"}


def _arches_compatible(stored_os: str, stored_arch: str, query_arch: str) -> bool:
    """True if a stored `(os, arch)` entry should match a query arch.

    Exact equality always matches. The universal2/universal special case
    only kicks in on darwin — that's the only ecosystem where fat
    binaries are a real distribution convention.
    """
    if stored_arch == query_arch:
        return True
    if stored_os == "darwin" and stored_arch in _UNIVERSAL_DARWIN_ARCHES:
        return query_arch in _UNIVERSAL_DARWIN_COVERS
    return False


def platform_matches(stored: dict[str, Any], query: dict[str, Any]) -> bool:
    """True iff `stored` and `query` agree on every field present in both.

    A field present in `query` but missing/empty in `stored` is a wildcard
    in `stored` (the producer didn't constrain it) and matches. A field
    present in `stored` but missing/empty in `query` is a wildcard in the
    query (the caller doesn't care) and matches.

    `features` is a list; the query's features must be a subset of stored's.

    `arch` uses universal-arch compatibility on darwin — see
    `_arches_compatible`.
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
        elif key == "arch":
            if not _arches_compatible(stored.get("os", ""), sval, qval):
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
        elif key == "arch":
            if not _arches_compatible(stored.get("os", ""), sval, qval):
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


def _specificity(stored: dict[str, Any], query: dict[str, Any]) -> int:
    """Count fields that are explicitly set in BOTH stored and query (and equal).

    Used for specificity-priority resolution: when multiple stored platforms
    wildcard-match a query, prefer the one whose constraints most precisely
    align with the query. CSS-selector / OCI-image-index convention.
    """
    score = 0
    for key, qval in query.items():
        if not qval:
            continue
        sval = stored.get(key)
        if not sval:
            continue
        if key == "features":
            # Each matched feature contributes one point.
            score += len(set(qval) & set(sval))
        elif sval == qval:
            score += 1
    return score


def resolve_in_catalog(
    catalog: dict[str, Any],
    tool: str,
    platform: dict[str, Any],
    channel: str,
    variant: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a (tool, platform, channel, variant) query against a Catalog
    document. Returns the matching Asset dict.

    Semantics (DESIGN.md §5):
      1. Filter platforms by `platform_matches` AND `variant_matches`
         (wildcard semantics — missing field on either side matches).
      2. Of the surviving candidates, keep only those with maximum
         combined platform+variant specificity (CSS-selector style).
      3. If exactly one survives, return its asset.
         If zero, raise NoMatchingAssetError.
         If >1, raise AmbiguityError (true tie — producer published two
         equally-specific entries the resolver cannot disambiguate).

    Step 2 is what lets a producer publish e.g.
        {os:linux, arch:x86_64}                (fallback, no libc claim)
        {os:linux, arch:x86_64, libc:musl}     (explicit musl variant)
    and have a `libc:musl` query unambiguously hit the explicit entry.

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

    query_variant = variant or {}
    matches: list[tuple[int, dict[str, Any]]] = []
    for rp in release.get("platforms", []):
        stored_platform = rp.get("platform", {}) or {}
        stored_variant = rp.get("variant", {}) or {}
        if not platform_matches(stored_platform, platform):
            continue
        if not variant_matches(stored_variant, query_variant):
            continue
        score = (
            _specificity(stored_platform, platform)
            + _specificity(stored_variant, query_variant)
        )
        matches.append((score, rp))

    if not matches:
        raise NoMatchingAssetError(
            f"no asset in release {version!r} matches "
            f"platform={platform!r} variant={variant!r}"
        )

    # Keep only the maximum-specificity matches (CSS-selector style).
    best_score = max(s for s, _ in matches)
    winners = [rp for s, rp in matches if s == best_score]

    if len(winners) > 1:
        raise AmbiguityError(winners)

    return winners[0]["asset"]


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
