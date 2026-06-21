"""Shared pytest fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO


@pytest.fixture(scope="session")
def examples_dir() -> Path:
    return EXAMPLES


def _load(rel: str) -> dict:
    return json.loads((EXAMPLES / rel).read_text(encoding="utf-8"))


@pytest.fixture
def catalog() -> dict:
    return _load("catalog.json")


@pytest.fixture
def catalog_bytes() -> bytes:
    return (EXAMPLES / "catalog.json").read_bytes()


@pytest.fixture
def index() -> dict:
    return _load("index.json")


@pytest.fixture
def github_release() -> dict:
    return _load("github_release.json")


@pytest.fixture
def embedded_slice() -> dict:
    return _load("embedded_slice.json")


@pytest.fixture
def multipart_release() -> dict:
    return _load("multipart_release.json")
