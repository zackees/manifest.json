#!/usr/bin/env python3
"""Copy the repo-root canonical artifacts (manifest.proto, manifest.schema.json)
into v1/ so the GitHub Pages URLs serve a stable per-version snapshot.

While the project is on schema v1, the root files and v1/ files MUST be
byte-identical. CI runs this script and then `git diff --exit-code` to
detect drift between the canonical files and the published v1 snapshot.

When v2 ships in the future, the root files will track v2 while v1/
stays frozen — at that point this script will be replaced with a
version-bump tool.
"""

from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAIRS = [
    (REPO / "manifest.proto",        REPO / "v1" / "manifest.proto"),
    (REPO / "manifest.schema.json",  REPO / "v1" / "manifest.schema.json"),
]


def main() -> int:
    REPO.joinpath("v1").mkdir(exist_ok=True)
    for src, dst in PAIRS:
        if not src.exists():
            print(f"  missing {src}", file=sys.stderr)
            return 1
        if dst.exists() and filecmp.cmp(src, dst, shallow=False):
            print(f"  ok    {dst.relative_to(REPO)} (unchanged)")
            continue
        shutil.copy2(src, dst)
        print(f"  wrote {dst.relative_to(REPO)} ({dst.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
