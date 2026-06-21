#!/usr/bin/env python3
"""Compile proto/manifest.proto -> src/manifest_json/proto/manifest_pb2.py.

Uses grpcio-tools' bundled protoc so this works cross-platform without an
external `protoc` binary on PATH.

Run after editing the .proto. CI runs this and then `git diff --exit-code`
to detect drift between committed bindings and the proto source of truth.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROTO_SRC = REPO / "proto" / "manifest.proto"
OUT_DIR = REPO / "src" / "manifest_json" / "proto"


def main() -> int:
    if not PROTO_SRC.exists():
        print(f"missing {PROTO_SRC}", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"--proto_path={PROTO_SRC.parent}",
        f"--python_out={OUT_DIR}",
        str(PROTO_SRC),
    ]
    print("$ " + " ".join(cmd))
    rc = subprocess.run(cmd, check=False).returncode
    if rc != 0:
        return rc

    # protoc emits `manifest_pb2.py` at OUT_DIR root with a top-level
    # `import manifest_pb2 as ...` style — that's exactly what we want
    # since we import via `manifest_json.proto.manifest_pb2`.
    generated = OUT_DIR / "manifest_pb2.py"
    if not generated.exists():
        print(f"expected {generated} to be generated", file=sys.stderr)
        return 1

    # Ensure trailing newline (proto's emitter is consistent, but be safe).
    content = generated.read_bytes()
    if not content.endswith(b"\n"):
        generated.write_bytes(content + b"\n")

    print(f"wrote {generated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
