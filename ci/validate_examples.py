#!/usr/bin/env python3
"""Validate every manifest under examples/ against the schema + semantic rules."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from manifest_json.validate import ValidationError, validate_document  # noqa: E402

EXAMPLES = REPO / "examples"


def main() -> int:
    if not EXAMPLES.exists():
        print(f"no examples/ dir at {EXAMPLES}", file=sys.stderr)
        return 2
    rc = 0
    paths = sorted(EXAMPLES.rglob("*.json"))
    if not paths:
        print("no example JSON files found", file=sys.stderr)
        return 1
    for p in paths:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            validate_document(doc)
        except (ValidationError, json.JSONDecodeError) as exc:
            print(f"FAIL {p.relative_to(REPO)}: {exc}", file=sys.stderr)
            rc = 1
        else:
            print(f"OK   {p.relative_to(REPO)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
