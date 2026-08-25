"""RED contract tests for the multipart Asset transport union.

These tests are the Phase-0 scaffold for soldr-toolchain#149.  They exercise
the public ``validate_document`` entry point so every containing document is
forced through one shared semantic contract.
"""

from __future__ import annotations

import copy
import random

import pytest

from manifest_json.validate import ValidationError, validate_document


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
    for _ in range(64):
        sizes = [randomizer.randint(1, 1_000_000) for _ in range(randomizer.randint(1, 16))]
        valid = copy.deepcopy(multipart_release)
        valid["platforms"][0]["asset"] = _multipart_asset(sizes)
        validate_document(valid)

        mutated = copy.deepcopy(valid)
        victim = randomizer.randrange(len(sizes))
        mutated["platforms"][0]["asset"]["parts"][victim]["sha256"] = "0" * 63 + "G"
        with pytest.raises(ValidationError, match="sha256"):
            validate_document(mutated)
