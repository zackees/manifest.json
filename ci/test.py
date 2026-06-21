#!/usr/bin/env python3
"""Run pytest. Thin wrapper so the GH Action just shells out to one script."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    cmd = [sys.executable, "-m", "pytest", str(REPO / "tests")]
    print("$ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=REPO, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
