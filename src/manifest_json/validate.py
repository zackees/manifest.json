"""Validate a manifest.json document against the JSON Schema AND the
semantic rules from DESIGN.md §5 + §8.2."""

from __future__ import annotations

import re
from typing import Any

import jsonschema

from manifest_json.schema import generate_json_schema

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# Recognized canonical pattern for $schema URLs:
#   https://.../<owner>/manifest.json/v<N>/manifest.schema.json
# Used only for the cross-check: if the URL embeds a version, it must
# equal `schema_version`. Unknown URL shapes (mirrors, airgapped hosting)
# are tolerated and skip the check.
_SCHEMA_URL_VERSION_RE = re.compile(r"/v(\d+)/manifest\.schema\.json(?:[?#]|$)")


class ValidationError(Exception):
    """Raised when a document fails structural or semantic validation."""


def _validate_against_schema(doc: dict[str, Any]) -> None:
    schema = generate_json_schema()
    try:
        jsonschema.validate(doc, schema)
    except jsonschema.ValidationError as exc:
        raise ValidationError(f"schema violation: {exc.message}") from exc


def _check_sha256(value: str, where: str) -> None:
    if value and not _SHA256_RE.match(value):
        raise ValidationError(f"{where}: {value!r} is not a 64-char lowercase hex sha256")


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
            asset = rp.get("asset", {}) or {}
            _check_sha256(asset.get("sha256", ""), where=f"release {release.get('version')!r} asset")

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
        asset = rp.get("asset", {}) or {}
        _check_sha256(asset.get("sha256", ""), where="release asset")


def _validate_embedded_slice_semantics(doc: dict[str, Any]) -> None:
    if not doc.get("schema_version"):
        raise ValidationError("EmbeddedSlice: `schema_version` is required and >0")
    if not doc.get("tool"):
        raise ValidationError("EmbeddedSlice: `tool` is required and non-empty")
    if not doc.get("compiled_version"):
        raise ValidationError("EmbeddedSlice: `compiled_version` is required")
    asset = doc.get("asset", {}) or {}
    _check_sha256(asset.get("sha256", ""), where="embedded slice asset")
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
