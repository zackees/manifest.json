#!/usr/bin/env python3
"""Generate manifest.schema.json (repo root) from the compiled proto descriptors.

Calls into `manifest_json.schema.generate_json_schema()` and writes the
result deterministically. CI runs this and then `git diff --exit-code` so
schema drift fails the build.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from manifest_json.schema import generate_json_schema  # noqa: E402

OUT = REPO / "manifest.schema.json"


def main() -> int:
    schema = generate_json_schema()
    text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
