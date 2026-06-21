#!/usr/bin/env python3
"""Fail if any tracked file changed after running the generators.

Used by CI to detect when the committed proto bindings or JSON Schema
have drifted from the canonical proto source.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--exit-code"],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("no drift")
        return 0
    print("FAIL: generated files differ from committed copies", file=sys.stderr)
    print(result.stdout, file=sys.stderr)
    print("Run `python ci/gen_proto.py && python ci/gen_schema.py` and commit.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
