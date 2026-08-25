"""RED contract tests for the multipart Asset transport union.

These tests are the Phase-0 scaffold for soldr-toolchain#149.  They exercise
the public ``validate_document`` entry point so every containing document is
forced through one shared semantic contract.
"""

from __future__ import annotations

import copy
import random

import pytest

from manifest_json.validate import (
    MAX_ASSET_BYTES,
    MAX_PARTS,
    MAX_URL_BYTES,
    ValidationError,
    validate_document,
)


def _platform_asset(document: dict) -> dict:
    if document["kind"] == "Catalog":
        return document["releases"][0]["platforms"][0]["asset"]
    if document["kind"] == "Release":
        return document["platforms"][0]["asset"]
    if document["kind"] == "EmbeddedSlice":
        return document["asset"]
    raise AssertionError(f"unsupported fixture kind: {document['kind']}")


def _multipart_asset(sizes: list[int]) -> dict:
    parts = []
    for number, size in enumerate(sizes, start=1):
        digest = f"{number:064x}"
        parts.append(
            {
                "number": number,
                "sha256": digest,
                "size_bytes": size,
                "urls": [f"https://example.invalid/{number}-{digest}.part"],
            }
        )
    return {
        "filename": "payload.tar.zst",
        "size_bytes": sum(sizes),
        "sha256": "f" * 64,
        "urls": [],
        "parts": parts,
    }


def _documents_with_asset(
    catalog: dict, multipart_release: dict, embedded_slice: dict, asset: dict
) -> list[dict]:
    release = copy.deepcopy(multipart_release)
    release["platforms"][0]["asset"] = copy.deepcopy(asset)

    catalog_doc = copy.deepcopy(catalog)
    catalog_doc["releases"][0]["platforms"][0]["asset"] = copy.deepcopy(asset)
    catalog_doc["releases"][0]["min_client_version"] = 2

    embedded = copy.deepcopy(embedded_slice)
    embedded["asset"] = copy.deepcopy(asset)
    return [catalog_doc, release, embedded]


@pytest.mark.parametrize("kind_index", range(3), ids=["catalog", "release", "embedded"])
def test_direct_and_multipart_transports_validate_in_every_asset_location(
    kind_index: int,
    catalog: dict,
    multipart_release: dict,
    embedded_slice: dict,
) -> None:
    multipart = _documents_with_asset(
        catalog, multipart_release, embedded_slice, _multipart_asset([3, 5, 7])
    )[kind_index]
    validate_document(multipart)

    direct_asset = _multipart_asset([15])
    direct_asset["urls"] = ["https://example.invalid/payload.tar.zst"]
    direct_asset["parts"] = []
    direct = _documents_with_asset(
        catalog, multipart_release, embedded_slice, direct_asset
    )[kind_index]
    validate_document(direct)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda asset: asset["urls"].append("https://example.invalid/full"), "exactly one"),
        (lambda asset: asset.update(urls=[], parts=[]), "exactly one"),
        (lambda asset: asset["parts"][0].update(number=0), "number"),
        (lambda asset: asset["parts"][1].update(number=3), "contiguous"),
        (lambda asset: asset["parts"].reverse(), "order"),
        (lambda asset: asset["parts"][0].update(size_bytes=0), "size_bytes"),
        (lambda asset: asset["parts"][0].update(sha256="BAD"), "sha256"),
        (lambda asset: asset["parts"][0].update(urls=[]), "urls"),
        (
            lambda asset: asset["parts"][0]["urls"].append(
                asset["parts"][0]["urls"][0]
            ),
            "duplicate",
        ),
        (lambda asset: asset.update(size_bytes=asset["size_bytes"] + 1), "sum"),
    ],
    ids=[
        "both-transports",
        "neither-transport",
        "zero-number",
        "number-gap",
        "out-of-order",
        "zero-size",
        "bad-digest",
        "no-mirror",
        "duplicate-url",
        "sum-mismatch",
    ],
)
def test_multipart_invariants_are_rejected(
    mutation,
    message: str,
    multipart_release: dict,
) -> None:
    document = copy.deepcopy(multipart_release)
    asset = document["platforms"][0]["asset"]
    mutation(asset)
    with pytest.raises(ValidationError, match=message):
        validate_document(document)


def test_component_assets_use_the_same_transport_semantics(
    multipart_release: dict,
) -> None:
    document = copy.deepcopy(multipart_release)
    component_asset = document["components"][0]["asset"]
    component_asset["parts"] = [
        {
            "number": 1,
            "sha256": "a" * 64,
            "size_bytes": component_asset["size_bytes"],
            "urls": ["https://example.invalid/component.part"],
        }
    ]
    with pytest.raises(ValidationError, match="exactly one"):
        validate_document(document)


def test_randomized_valid_partitions_and_single_mutations(
    multipart_release: dict,
) -> None:
    randomizer = random.Random(149)
    # Validation regenerates a complete schema on each public invocation; a
    # dozen seeded, varied partitions keeps this property coverage practical.
    for _ in range(12):
        sizes = [randomizer.randint(1, 1_000_000) for _ in range(randomizer.randint(1, 16))]
        valid = copy.deepcopy(multipart_release)
        valid["platforms"][0]["asset"] = _multipart_asset(sizes)
        validate_document(valid)

        mutated = copy.deepcopy(valid)
        victim = randomizer.randrange(len(sizes))
        mutated["platforms"][0]["asset"]["parts"][victim]["sha256"] = "0" * 63 + "G"
        with pytest.raises(ValidationError, match="sha256"):
            validate_document(mutated)

        # Exercise independent mutations across varied layouts.  The same
        # semantic path is used for Catalog platform and component assets.
        for action in ("drop", "duplicate", "reorder", "resize", "digest"):
            broken = copy.deepcopy(valid)
            parts = broken["platforms"][0]["asset"]["parts"]
            if action == "drop": parts.pop(victim)
            elif action == "duplicate": parts.insert(victim, copy.deepcopy(parts[victim]))
            elif action == "reorder" and len(parts) > 1: parts[0], parts[-1] = parts[-1], parts[0]
            elif action == "resize": parts[victim]["size_bytes"] += 1
            else: parts[victim]["sha256"] = "f" * 63 + "z"
            with pytest.raises(ValidationError): validate_document(broken)


@pytest.mark.parametrize("hostile", [True, 0, -1, 1 << 64, "1", 1.0])
def test_part_number_exact_type_and_hostile_bounds(hostile, multipart_release: dict) -> None:
    document=copy.deepcopy(multipart_release)
    document["platforms"][0]["asset"]["parts"][0]["number"]=hostile
    with pytest.raises(ValidationError): validate_document(document)


@pytest.mark.parametrize("url", ["%2e%2e/x", "%252e%252e/x", "%255cfile", "part?token=secret", "part#fragment"])
def test_relative_url_encoding_bypasses_are_rejected(url: str, multipart_release: dict) -> None:
    document=copy.deepcopy(multipart_release)
    asset=document["platforms"][0]["asset"]; asset["urls"]=[url]; asset["parts"]=[]
    with pytest.raises(ValidationError): validate_document(document)


def test_signed_absolute_query_is_accepted(multipart_release: dict) -> None:
    document=copy.deepcopy(multipart_release)
    asset=document["platforms"][0]["asset"]; asset["urls"]=["https://mirror.example/file?X-Amz-Signature=opaque"]; asset["parts"]=[]
    validate_document(document)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda a: a.update(urls=["http://example.invalid/file"], parts=[]), "HTTPS"),
        (lambda a: a.update(urls=["//example.invalid/file"], parts=[]), "safe relative"),
        (lambda a: a.update(urls=["relative/file"], parts=[]), None),
        (lambda a: a.update(urls=["https://example.invalid/" + "x" * MAX_URL_BYTES], parts=[]), "8192"),
    ],
    ids=["http-rejected", "network-relative-rejected", "relative-accepted", "url-cap"],
)
def test_direct_url_safety_and_length(mutate, message, multipart_release: dict) -> None:
    document = copy.deepcopy(multipart_release)
    asset = document["platforms"][0]["asset"]
    mutate(asset)
    if message is None:
        validate_document(document)
    else:
        with pytest.raises(ValidationError, match=message):
            validate_document(document)


def test_multipart_limits_and_capability(multipart_release: dict) -> None:
    too_many = copy.deepcopy(multipart_release)
    asset = too_many["platforms"][0]["asset"]
    asset["parts"] = _multipart_asset([1] * (MAX_PARTS + 1))["parts"]
    asset["size_bytes"] = MAX_PARTS + 1
    with pytest.raises(ValidationError, match="4096"):
        validate_document(too_many)

    oversized = copy.deepcopy(multipart_release)
    oversized["platforms"][0]["asset"] = _multipart_asset([MAX_ASSET_BYTES + 1])
    with pytest.raises(ValidationError, match="limit"):
        validate_document(oversized)

    unsupported = copy.deepcopy(multipart_release)
    unsupported["min_client_version"] = 1
    with pytest.raises(ValidationError, match="min_client_version"):
        validate_document(unsupported)
