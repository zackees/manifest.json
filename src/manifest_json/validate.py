"""Validate a manifest.json document against the JSON Schema AND the
semantic rules from DESIGN.md §5 + §8.2."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlsplit

import jsonschema

from manifest_json.schema import generate_json_schema

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# Recognized canonical pattern for $schema URLs:
#   https://.../<owner>/manifest.json/v<N>/manifest.schema.json
# Used only for the cross-check: if the URL embeds a version, it must
# equal `schema_version`. Unknown URL shapes (mirrors, airgapped hosting)
# are tolerated and skip the check.
_SCHEMA_URL_VERSION_RE = re.compile(r"/v(\d+)/manifest\.schema\.json(?:[?#]|$)")
MAX_PARTS = 4096
MAX_ASSET_BYTES = 8 * 1024**4
MAX_URL_BYTES = 8192
MAX_U64 = (1 << 64) - 1


class ValidationError(Exception):
    """Raised when a document fails structural or semantic validation."""


def _validate_against_schema(doc: dict[str, Any]) -> None:
    schema = generate_json_schema()
    try:
        jsonschema.validate(doc, schema)
    except jsonschema.ValidationError as exc:
        raise ValidationError(f"schema violation: {exc.message}") from exc


def _check_sha256(value: str, where: str, *, required: bool = False) -> None:
    if (required and not value) or (value and not _SHA256_RE.match(value)):
        raise ValidationError(f"{where}: {value!r} is not a 64-char lowercase hex sha256")


def _validate_url(url: str, where: str) -> None:
    """Accept HTTPS origins or safe, base-relative references only.

    Relative references deliberately need a caller-supplied trusted base; this
    validator only establishes that they cannot smuggle an origin or scheme.
    """
    if not isinstance(url, str) or not url:
        raise ValidationError(f"{where}: URL is required")
    if len(url.encode("utf-8")) > MAX_URL_BYTES:
        raise ValidationError(f"{where}: URL exceeds {MAX_URL_BYTES} UTF-8 bytes")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in url):
        raise ValidationError(f"{where}: URL contains a control character")
    decoded = url
    # Repeated decoding closes the otherwise easy %252e%252e bypass.
    for _ in range(8):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in decoded):
        raise ValidationError(f"{where}: URL contains a control character")
    parsed = urlsplit(url)
    if parsed.scheme:
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValidationError(f"{where}: URL must be an HTTPS URL without credentials")
        if parsed.fragment or "\\" in decoded or any(part == ".." for part in urlsplit(decoded).path.split("/")):
            raise ValidationError(f"{where}: URL contains an unsafe path or fragment")
        return
    decoded_parsed = urlsplit(decoded)
    if ("%" in url or parsed.netloc or not parsed.path or url.startswith("//") or "\\" in decoded
            or parsed.query or parsed.fragment or decoded_parsed.query or decoded_parsed.fragment
            or any(part == ".." for part in decoded_parsed.path.split("/"))):
        raise ValidationError(f"{where}: URL must be HTTPS absolute or a safe relative path")


def _validate_urls(urls: Any, where: str) -> None:
    if not isinstance(urls, list) or not urls:
        raise ValidationError(f"{where}: urls must contain at least one mirror")
    if len(set(urls)) != len(urls):
        raise ValidationError(f"{where}: duplicate URL")
    for index, url in enumerate(urls):
        _validate_url(url, f"{where} URL[{index}]")


def _positive_u64(value: Any, where: str, cap: int = MAX_U64) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(f"{where}: size_bytes must be positive")
    if value > MAX_U64 or value > cap:
        raise ValidationError(f"{where}: size_bytes exceeds supported limit")
    return value


def _validate_asset_semantics(asset: dict[str, Any], where: str) -> None:
    """Validate the transport union shared by every Asset containing shape."""
    urls = asset.get("urls") or []
    parts = asset.get("parts") or []
    if bool(urls) == bool(parts):
        raise ValidationError(f"{where}: exactly one of urls or parts must be non-empty")
    asset_size = _positive_u64(asset.get("size_bytes"), where, MAX_ASSET_BYTES)
    _check_sha256(asset.get("sha256", ""), where=f"{where} sha256", required=True)
    if urls:
        _validate_urls(urls, where)
        return
    if not isinstance(parts, list) or len(parts) > MAX_PARTS:
        raise ValidationError(f"{where}: parts may contain at most {MAX_PARTS} records")
    total = 0
    seen_records: set[tuple[Any, Any]] = set()
    for expected_number, part in enumerate(parts, start=1):
        part_where = f"{where} part {expected_number}"
        number = part.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number != expected_number:
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                raise ValidationError(f"{part_where}: number must be positive and 1-based")
            raise ValidationError(f"{part_where}: parts must be contiguous and in order")
        digest = part.get("sha256", "")
        record = (number, digest)
        if record in seen_records:
            raise ValidationError(f"{part_where}: duplicate part record")
        seen_records.add(record)
        _check_sha256(digest, where=f"{part_where} sha256", required=True)
        size = _positive_u64(part.get("size_bytes"), part_where, MAX_ASSET_BYTES)
        if total > MAX_U64 - size or total + size > MAX_ASSET_BYTES:
            raise ValidationError(f"{where}: part size sum overflows or exceeds supported limit")
        total += size
        _validate_urls(part.get("urls"), part_where)
    if total != asset_size:
        raise ValidationError(f"{where}: part size sum {total} does not match asset size_bytes {asset_size}")


def validate_catalog_semantics(catalog: dict[str, Any]) -> None:
    """Catalog-specific semantic rules:
      - `tool` and `schema_version` must be set
      - every channels[name] resolves to a version in releases[]
      - no duplicate (platform, variant) within a Release
      - releases sorted newest-first by `published_at` (informative — warns
        only if both are RFC3339-comparable strings)
    """
    if catalog.get("kind") != "Catalog":
        return
    if not catalog.get("tool"):
        raise ValidationError("Catalog: `tool` is required and non-empty")
    if not catalog.get("schema_version"):
        raise ValidationError("Catalog: `schema_version` is required and >0")
    versions = {r.get("version") for r in catalog.get("releases", [])}
    for channel, version in catalog.get("channels", {}).items():
        if version not in versions:
            raise ValidationError(
                f"channel {channel!r} -> {version!r}, but no release with that version"
            )

    for release in catalog.get("releases", []):
        _validate_source(
            release.get("source") or {}, where=f"release {release.get('version')!r}"
        )
        seen = set()
        for rp in release.get("platforms", []):
            key = (
                tuple(sorted((rp.get("platform") or {}).items())),
                tuple(sorted((rp.get("variant") or {}).items())),
            )
            if key in seen:
                raise ValidationError(
                    f"release {release.get('version')!r} has duplicate "
                    f"(platform, variant): {key}"
                )
            seen.add(key)
            _validate_asset_semantics(
                rp.get("asset", {}) or {},
                where=f"release {release.get('version')!r} platform asset",
            )
        _validate_release_components(release)
        _require_multipart_capability(release)

    # Soft check: published_at ordering
    pubs = [r.get("published_at", "") for r in catalog.get("releases", [])]
    pubs_present = [p for p in pubs if p]
    if pubs_present == sorted(pubs_present, reverse=True):
        return
    if all(pubs):
        raise ValidationError(
            "releases[] must be sorted newest-first by published_at"
        )


def _validate_index_semantics(doc: dict[str, Any]) -> None:
    if not doc.get("schema_version"):
        raise ValidationError("Index: `schema_version` is required and >0")
    for tool_name, entry in (doc.get("tools") or {}).items():
        desc = (entry or {}).get("descriptor", {}) or {}
        url = desc.get("url", "")
        if not url:
            raise ValidationError(f"tool {tool_name!r}: descriptor.url is required")
        _check_sha256(desc.get("sha256", ""), where=f"tool {tool_name!r} descriptor")


def _validate_source(source: dict[str, Any], where: str) -> None:
    if not source:
        return
    has_vcs = bool(source.get("repo_url")) and bool(source.get("ref"))
    has_archive = bool(source.get("archive_url"))
    if not has_vcs and not has_archive:
        raise ValidationError(
            f"{where}: Source must declare either (repo_url + ref) or archive_url"
        )
    _check_sha256(source.get("archive_sha256", ""), where=f"{where} archive_sha256")


def _validate_release_semantics(release: dict[str, Any]) -> None:
    if not release.get("schema_version"):
        raise ValidationError("Release: `schema_version` is required and >0")
    if not release.get("tool"):
        raise ValidationError("Release: `tool` is required and non-empty")
    if not release.get("version"):
        raise ValidationError("Release: `version` is required and non-empty")
    _validate_source(release.get("source") or {}, where=f"release {release.get('version')!r}")
    seen = set()
    for rp in release.get("platforms", []):
        key = (
            tuple(sorted((rp.get("platform") or {}).items())),
            tuple(sorted((rp.get("variant") or {}).items())),
        )
        if key in seen:
            raise ValidationError(f"duplicate (platform, variant): {key}")
        seen.add(key)
        _validate_asset_semantics(rp.get("asset", {}) or {}, where="release platform asset")
    _validate_release_components(release)
    _require_multipart_capability(release)


def _validate_release_components(release: dict[str, Any]) -> None:
    for component in release.get("components", []):
        _validate_asset_semantics(
            component.get("asset", {}) or {},
            where=f"release component {component.get('id')!r} asset",
        )


def _require_multipart_capability(release: dict[str, Any]) -> None:
    assets = [rp.get("asset", {}) or {} for rp in release.get("platforms", [])]
    assets.extend(component.get("asset", {}) or {} for component in release.get("components", []))
    minimum = release.get("min_client_version", 0)
    if isinstance(minimum, bool) or not isinstance(minimum, int):
        raise ValidationError("min_client_version must be an integer")
    if any(asset.get("parts") for asset in assets) and minimum < 2:
        raise ValidationError("multipart assets require min_client_version >= 2")


def _validate_embedded_slice_semantics(doc: dict[str, Any]) -> None:
    if not doc.get("schema_version"):
        raise ValidationError("EmbeddedSlice: `schema_version` is required and >0")
    if not doc.get("tool"):
        raise ValidationError("EmbeddedSlice: `tool` is required and non-empty")
    if not doc.get("compiled_version"):
        raise ValidationError("EmbeddedSlice: `compiled_version` is required")
    asset = doc.get("asset", {}) or {}
    _validate_asset_semantics(asset, where="embedded slice asset")
    _check_sha256(doc.get("online_sha256", ""), where="embedded slice online_sha256")


def _validate_archive_contents_semantics(doc: dict[str, Any]) -> None:
    if not doc.get("schema_version"):
        raise ValidationError("ArchiveContents: `schema_version` is required and >0")
    if not doc.get("asset_sha256"):
        raise ValidationError("ArchiveContents: `asset_sha256` is required")
    _check_sha256(doc.get("asset_sha256", ""), where="ArchiveContents asset_sha256")

    files = doc.get("files") or []
    valid_types = {"file", "dir", "symlink", "hardlink"}
    seen_paths: set[str] = set()
    for entry in files:
        path = entry.get("path", "")
        if not path:
            raise ValidationError("ArchiveContents file: `path` is required")
        if "\\" in path:
            raise ValidationError(
                f"ArchiveContents file path {path!r}: must use forward slashes"
            )
        if path in seen_paths:
            raise ValidationError(f"ArchiveContents has duplicate path {path!r}")
        seen_paths.add(path)
        etype = entry.get("type", "")
        if etype and etype not in valid_types:
            raise ValidationError(
                f"ArchiveContents file {path!r}: type must be one of "
                f"{sorted(valid_types)}, got {etype!r}"
            )
        if etype in ("symlink", "hardlink") and not entry.get("linkname"):
            raise ValidationError(
                f"ArchiveContents file {path!r}: {etype} requires `linkname`"
            )
        _check_sha256(entry.get("sha256", ""), where=f"ArchiveContents file {path!r}")

    declared = doc.get("file_count", 0)
    if declared and declared != len(files):
        raise ValidationError(
            f"ArchiveContents: file_count={declared} but files[] has {len(files)} entries"
        )


def _validate_schema_url(doc: dict[str, Any]) -> None:
    """If $schema is present AND the URL embeds a version per the
    canonical pattern, it MUST match schema_version. URLs that don't
    match the canonical pattern (mirrors, internal hosting) are tolerated
    and skip the cross-check."""
    url = doc.get("$schema", "")
    if not url:
        return
    m = _SCHEMA_URL_VERSION_RE.search(url)
    if not m:
        return  # unknown URL shape — tolerated
    url_version = int(m.group(1))
    doc_version = doc.get("schema_version", 0)
    if url_version != doc_version:
        raise ValidationError(
            f"$schema URL implies v{url_version} but schema_version={doc_version}"
        )


def validate_document(doc: dict[str, Any]) -> None:
    """Full structural + semantic validation. Raises ValidationError on any
    violation. Returns None on success."""
    _validate_against_schema(doc)
    _validate_schema_url(doc)
    kind = doc.get("kind")
    if kind == "Catalog":
        validate_catalog_semantics(doc)
    elif kind == "Index":
        _validate_index_semantics(doc)
    elif kind == "Release":
        _validate_release_semantics(doc)
    elif kind == "EmbeddedSlice":
        _validate_embedded_slice_semantics(doc)
    elif kind == "ArchiveContents":
        _validate_archive_contents_semantics(doc)
    else:
        raise ValidationError(f"unknown kind {kind!r}")


__all__ = ["ValidationError", "validate_catalog_semantics", "validate_document"]
