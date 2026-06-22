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


class TripleParseError(ResolveError):
    """A rustc-style target triple could not be parsed into a v1 Platform.

    Raised for malformed triples (fewer than 3 segments) and for unknown
    vendors. Vendor is load-bearing — `uwp`, `win7`, `unikraft`, `koji`
    name distinct rustc targets that share os/arch/abi with a canonical
    sibling but ship different artifacts (see issue #7 lesson 1). Rather
    than silently dropping the vendor on the floor, we refuse to parse.
    Unknown arches and OSes pass through unchanged so the downstream
    resolver fails closed (NoMatchingAssetError) rather than fuzzy-merging
    near-arches like `x86_64h` into `x86_64`.
    """


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


# Query-side normalization. Canonical values are `x86_64`, `aarch64`,
# `armv7`, `riscv64`, `wasm32` (per DESIGN.md §3.1). Callers commonly
# use looser conventions inherited from the package-manager they grew up
# with — npm uses `x64`/`arm64`, Debian/Docker use `amd64`/`arm64`, some
# users still type `x86` meaning "modern Intel desktop chip" (which is
# really x86_64 / amd64). Normalize on the query side so the resolver
# does what they meant.
#
# Note: `x86` historically means 32-bit (i386/i686). We map it to
# `x86_64` because in 2026 nobody asking for "x86" wants 32-bit; if you
# genuinely want 32-bit Intel, spell it `i686` or `i386` (those are
# left UNNORMALIZED — distinct from x86_64).
_ARCH_ALIASES: dict[str, str] = {
    # x86_64 family
    "x86_64":  "x86_64",
    "x64":     "x86_64",
    "amd64":   "x86_64",
    "x86-64":  "x86_64",
    "x86":     "x86_64",  # contentious but matches modern desktop usage
    # aarch64 family
    "aarch64": "aarch64",
    "arm64":   "aarch64",
    "arm":     "aarch64",  # modern "arm" almost always means 64-bit
    "armv8":   "aarch64",
    "armv8a":  "aarch64",
    # 32-bit ARM (kept distinct)
    "armv7":   "armv7",
    "armhf":   "armv7",
    "armv7l":  "armv7",
    "armv7a":  "armv7",
    # 32-bit Intel (kept distinct from x86_64 — see note above)
    "i686":    "i686",
    "i586":    "i686",
    "i486":    "i686",
    "i386":    "i686",
    "x86_32":  "i686",
    # riscv64 family
    "riscv64":   "riscv64",
    "riscv64gc": "riscv64",
    "rv64":      "riscv64",
    # 64-bit PowerPC little-endian (Debian `ppc64le` / rustc `powerpc64le`)
    "powerpc64le": "powerpc64le",
    "ppc64le":     "powerpc64le",
    # wasm
    "wasm32":  "wasm32",
    "wasm":    "wasm32",
    # universal — left alone, handled by _arches_compatible
    "universal":  "universal",
    "universal2": "universal2",
}

_OS_ALIASES: dict[str, str] = {
    "linux":    "linux",
    # darwin family
    "darwin":   "darwin",
    "macos":    "darwin",
    "mac":      "darwin",
    "osx":      "darwin",
    "macosx":   "darwin",
    # windows family
    "windows":  "windows",
    "win":      "windows",
    "win32":    "windows",
    "win64":    "windows",
    # bsd
    "freebsd":  "freebsd",
    "openbsd":  "openbsd",
    "netbsd":   "netbsd",
    # wasm
    "wasi":     "wasi",
}


# Some queries are genuinely ambiguous between 32-bit and 64-bit
# interpretations. `x86` historically means 32-bit Intel, but in modern
# desktop usage means 64-bit. `arm` could mean armv7 (legacy) or aarch64
# (modern). When a query uses one of these, resolve_all_in_catalog tries
# BOTH expansions in priority order — and surfaces both results if both
# match — so the caller can disambiguate visually.
#
# Single-canonical aliases (`x64`, `amd64`, etc.) expand to a single
# entry; the order is purely informational.
_ARCH_EXPANSIONS: dict[str, list[str]] = {
    # ambiguous: 64-bit first (modern desktop), 32-bit as fallback
    "x86":     ["x86_64", "i686"],
    "arm":     ["aarch64", "armv7"],
    # single-canonical (same as _ARCH_ALIASES but as a one-element list)
    "x86_64":  ["x86_64"],
    "x64":     ["x86_64"],
    "amd64":   ["x86_64"],
    "x86-64":  ["x86_64"],
    "aarch64": ["aarch64"],
    "arm64":   ["aarch64"],
    "armv7":   ["armv7"],
    "armhf":   ["armv7"],
    "armv7l":  ["armv7"],
    "i686":    ["i686"],
    "i386":    ["i686"],
    "x86_32":  ["i686"],
    "riscv64": ["riscv64"],
    "rv64":    ["riscv64"],
    "wasm32":  ["wasm32"],
    "wasm":    ["wasm32"],
    "universal":  ["universal"],
    "universal2": ["universal2"],
}


def _normalize_arch(arch: str) -> str:
    """Map a caller-side arch alias to its canonical form. Unknown values
    pass through unchanged — the resolver still treats them as opaque
    strings for equality comparison.

    For the single-result resolve_in_catalog this picks the FIRST entry
    in the expansion list (the preferred / modern interpretation).
    resolve_all_in_catalog uses `_expand_arch` to surface ALL candidates.
    """
    if not arch:
        return arch
    expansions = _ARCH_EXPANSIONS.get(arch.lower())
    if expansions:
        return expansions[0]
    return _ARCH_ALIASES.get(arch.lower(), arch)


def _expand_arch(arch: str) -> list[str]:
    """Return the ordered list of canonical arches a caller-side alias
    expands to. Used by resolve_all_in_catalog so an ambiguous query
    like `arch: "x86"` surfaces both `x86_64` matches (first) and
    `i686` matches (second) instead of silently picking one.
    """
    if not arch:
        return [""]
    expansions = _ARCH_EXPANSIONS.get(arch.lower())
    if expansions:
        return list(expansions)
    # Unknown / single-canonical via _ARCH_ALIASES — fall back to single.
    return [_ARCH_ALIASES.get(arch.lower(), arch)]


def _normalize_os(os_name: str) -> str:
    """Map a caller-side OS alias to its canonical form. See _normalize_arch."""
    if not os_name:
        return os_name
    return _OS_ALIASES.get(os_name.lower(), os_name)


def normalize_platform(platform: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of `platform` with `os` and `arch` normalized to
    canonical values. Other fields pass through unchanged.

    Producers should still publish canonical values in their manifests
    — normalization is meant for the consumer-facing query, not the
    producer-side stored form. Normalizing both sides would mask
    producer bugs (e.g. shipping `arm64` instead of `aarch64`).
    """
    if not platform:
        return platform
    out = dict(platform)
    if "os" in out and out["os"]:
        out["os"] = _normalize_os(out["os"])
    if "arch" in out and out["arch"]:
        out["arch"] = _normalize_arch(out["arch"])
    return out


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


# Closed allowlist of rustc-canonical vendors. v1 Platform has no
# `vendor` field by design — vendor isn't a resolver-relevant axis once
# you trust the producer's published tuple. But rustc target triples put
# vendor in a load-bearing slot: `x86_64-uwp-windows-msvc` and
# `x86_64-pc-windows-msvc` are different targets with different ABIs
# (issue #7 lesson 1). parse_target_triple refuses to silently drop the
# vendor on the floor; non-canonical vendors must be transcribed to v1
# by the producer, which forces the explicit "is this artifact really
# compatible with the canonical sibling?" decision.
_KNOWN_RUSTC_VENDORS: frozenset[str] = frozenset({"unknown", "pc", "apple"})


# rustc collapses libc + ABI into one trailing env slot. v1 splits them
# (Platform.libc vs Platform.abi). The translation is OS-aware because
# rustc reuses the token `gnu` to mean two different things: on Linux it
# means "glibc with the gnu ABI" (libc:glibc); on Windows it means
# "MinGW-w64 toolchain" (abi:gnu). _classify_rustc_env disambiguates.
_RUSTC_LIBC_TOKENS: frozenset[str] = frozenset({"musl", "uclibc", "newlib"})


def _classify_rustc_env(os_canonical: str, env: str) -> tuple[str, str]:
    """Map a rustc env-slot token to a v1 (field, value) pair.

    Unknown tokens default to the `abi` slot — the downstream resolver
    will then either find a matching abi entry or fail closed with
    NoMatchingAssetError, never silently miscompiling.
    """
    if os_canonical == "linux":
        if env == "gnu":
            # rustc "gnu" on Linux = glibc-with-gnu-ABI; v1 canonical libc value
            # is "glibc" (see examples/catalog.json, examples/github_release.json).
            return ("libc", "glibc")
        if env in _RUSTC_LIBC_TOKENS:
            return ("libc", env)
        return ("abi", env)
    if os_canonical == "windows":
        # All Windows env tokens (msvc, gnu, gnullvm) name an ABI/runtime,
        # not a libc — see examples/catalog.json windows entries.
        return ("abi", env)
    if env in _RUSTC_LIBC_TOKENS:
        return ("libc", env)
    return ("abi", env)


def parse_target_triple(triple: str) -> dict[str, Any]:
    """Parse a rustc-style target triple into a v1 Platform dict.

    Format: ``<arch>-<vendor>-<os>[-<env>]``. arch and os are normalized
    through the consumer alias map (so `amd64-unknown-linux-gnu` works
    even though rustc itself would spell it `x86_64-unknown-linux-gnu`).

    Examples:
        x86_64-unknown-linux-gnu   -> {os: linux,   arch: x86_64,  libc: glibc}
        x86_64-unknown-linux-musl  -> {os: linux,   arch: x86_64,  libc: musl}
        aarch64-apple-darwin       -> {os: darwin,  arch: aarch64}
        x86_64-pc-windows-msvc     -> {os: windows, arch: x86_64,  abi: msvc}
        x86_64-pc-windows-gnu      -> {os: windows, arch: x86_64,  abi: gnu}

    Raises:
        TripleParseError: empty string, fewer than 3 segments, or an
            unknown vendor. Unknown arches and OSes pass through
            unchanged — the resolver fails closed downstream rather
            than fuzzy-merging near-arches (issue #7 lesson 2).
    """
    if not triple:
        raise TripleParseError("empty triple")
    parts = triple.split("-")
    if len(parts) < 3:
        raise TripleParseError(
            f"triple {triple!r} has {len(parts)} segment(s); "
            f"expected at least 3 (arch-vendor-os[-env])"
        )
    arch_raw, vendor, os_raw = parts[0], parts[1], parts[2]
    env = parts[3] if len(parts) >= 4 else ""

    if vendor not in _KNOWN_RUSTC_VENDORS:
        raise TripleParseError(
            f"unknown vendor {vendor!r} in triple {triple!r}; "
            f"known: {sorted(_KNOWN_RUSTC_VENDORS)}. "
            f"Non-canonical vendors (uwp, win7, unikraft, koji, ...) name "
            f"distinct rustc targets — transcribe them to v1 explicitly "
            f"rather than parsing through this helper."
        )

    out: dict[str, Any] = {
        "arch": _normalize_arch(arch_raw),
        "os":   _normalize_os(os_raw),
    }
    if env:
        slot, value = _classify_rustc_env(out["os"], env)
        out[slot] = value
    return out


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

    # Normalize the caller's platform tuple. Aliases like `arm64`/`aarch64`
    # or `x64`/`x86_64` all map to canonical forms before matching, so a
    # consumer that came from npm-land (`x64`) or Debian-land (`amd64`)
    # gets the right answer without knowing our schema's exact vocabulary.
    # Producer-side (`stored_platform`) is NOT normalized — see
    # `normalize_platform` docstring.
    query_platform = normalize_platform(platform)
    query_variant = variant or {}
    matches: list[tuple[int, dict[str, Any]]] = []
    for rp in release.get("platforms", []):
        stored_platform = rp.get("platform", {}) or {}
        stored_variant = rp.get("variant", {}) or {}
        if not platform_matches(stored_platform, query_platform):
            continue
        if not variant_matches(stored_variant, query_variant):
            continue
        score = (
            _specificity(stored_platform, query_platform)
            + _specificity(stored_variant, query_variant)
        )
        matches.append((score, rp))

    if not matches:
        raise NoMatchingAssetError(
            f"no asset in release {version!r} matches "
            f"platform={platform!r} (normalized: {query_platform!r}) "
            f"variant={variant!r}"
        )

    # Keep only the maximum-specificity matches (CSS-selector style).
    best_score = max(s for s, _ in matches)
    winners = [rp for s, rp in matches if s == best_score]

    if len(winners) > 1:
        raise AmbiguityError(winners)

    return winners[0]["asset"]


def resolve_all_in_catalog(
    catalog: dict[str, Any],
    tool: str,
    platform: dict[str, Any],
    channel: str,
    variant: dict[str, Any] | None = None,
) -> list[Resolution]:
    """Return EVERY asset that matches a (tool, platform, channel, variant)
    query, ordered:
      1. by arch-expansion priority (x86_64 before i686 for `x86` queries)
      2. by specificity, descending (more-specific matches first)
      3. by filename, ascending (deterministic ordering when tied)

    Unlike resolve_in_catalog this NEVER raises AmbiguityError — when the
    caller's intent is ambiguous (e.g. `arch: "x86"` against a catalog
    that ships both x86_64 and i686 builds), both results come back and
    the caller picks. The first result is the preferred interpretation.

    Returns an empty list when no asset matches. Other resolver errors
    (SchemaError, ChannelNotFoundError, VersionNotInCatalogError)
    propagate normally — those are facts about the catalog, not the
    query.
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
    base_platform = dict(platform or {})
    base_platform["os"] = _normalize_os(base_platform.get("os", ""))
    raw_arch = base_platform.get("arch", "")
    arch_expansions = _expand_arch(raw_arch) if raw_arch else [""]

    # Collect candidates keyed by (asset filename, sha) so the same asset
    # surfacing under multiple arch expansions is deduped.
    seen: set[tuple[str, str]] = set()
    out: list[tuple[int, int, str, Resolution]] = []  # (arch_priority, -specificity, filename, Resolution)

    for arch_priority, query_arch in enumerate(arch_expansions):
        q = dict(base_platform)
        if query_arch:
            q["arch"] = query_arch
        for rp in release.get("platforms", []):
            stored_platform = rp.get("platform", {}) or {}
            stored_variant = rp.get("variant", {}) or {}
            if not platform_matches(stored_platform, q):
                continue
            if not variant_matches(stored_variant, query_variant):
                continue
            asset = rp.get("asset", {}) or {}
            key = (asset.get("filename", ""), asset.get("sha256", ""))
            if key in seen:
                continue
            seen.add(key)
            score = (
                _specificity(stored_platform, q)
                + _specificity(stored_variant, query_variant)
            )
            out.append((
                arch_priority,
                -score,
                asset.get("filename", ""),
                Resolution(kind="binary", asset=asset, version=version),
            ))

    out.sort(key=lambda t: (t[0], t[1], t[2]))
    return [r for _, _, _, r in out]


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
