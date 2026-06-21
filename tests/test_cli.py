"""Console-script entry points stay thin — these verify they wire up correctly."""

from __future__ import annotations

import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples"


def test_validate_cli_passes_on_examples(tmp_path: Path) -> None:
    from manifest_json.cli import validate_main
    rc = validate_main([str(EXAMPLES / "catalog.json"), str(EXAMPLES / "index.json")])
    assert rc == 0


def test_validate_cli_fails_on_bad_file(tmp_path: Path) -> None:
    from manifest_json.cli import validate_main
    bad = tmp_path / "bad.json"
    bad.write_text('{"kind": "Catalog"}', encoding="utf-8")
    rc = validate_main([str(bad)])
    assert rc == 1


def test_compile_cli_emits_slice(tmp_path: Path) -> None:
    from manifest_json.cli import compile_main
    out = tmp_path / "slice.json"
    rc = compile_main(
        [
            str(EXAMPLES / "catalog.json"),
            "--platform", "os=linux,arch=x86_64,libc=glibc",
            "--channel", "latest-stable",
            "-o", str(out),
        ]
    )
    assert rc == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["kind"] == "EmbeddedSlice"
    assert doc["compiled_version"] == "21.1.5"


def test_resolve_cli_returns_asset(tmp_path: Path, capsys) -> None:
    from manifest_json.cli import resolve_main
    rc = resolve_main(
        [
            str(EXAMPLES / "catalog.json"),
            "--tool", "clang",
            "--platform", "os=linux,arch=x86_64,libc=glibc",
            "--channel", "latest-stable",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "llvm-21.1.5-linux-x86_64.tar.zst" in out
