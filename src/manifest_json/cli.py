"""Console-script entry points. Kept thin — each wraps one library call."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from manifest_json.compile import compile_for_target, serialize_slice
from manifest_json.resolve import ResolveError, resolve_all_in_catalog, resolve_in_catalog
from manifest_json.schema import generate_json_schema
from manifest_json.validate import ValidationError, validate_document


def _read_json(path: Path) -> dict:
    with path.open("rb") as f:
        return json.load(f)


def _parse_kv(s: str) -> dict[str, str]:
    """Parse `os=linux,arch=x86_64,libc=glibc` into a dict."""
    out: dict[str, str] = {}
    if not s:
        return out
    for chunk in s.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError(f"expected key=value, got {chunk!r}")
        k, v = chunk.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def validate_main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="manifest-validate")
    p.add_argument("path", type=Path, nargs="+", help="manifest JSON file(s)")
    args = p.parse_args(argv)
    rc = 0
    for path in args.path:
        try:
            doc = _read_json(path)
            validate_document(doc)
        except (ValidationError, json.JSONDecodeError) as exc:
            print(f"FAIL {path}: {exc}", file=sys.stderr)
            rc = 1
        else:
            print(f"OK   {path}")
    return rc


def compile_main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="manifest-compile")
    p.add_argument("catalog", type=Path, help="Catalog JSON to compile from")
    p.add_argument("--platform", required=True, help="os=linux,arch=x86_64,libc=glibc")
    p.add_argument("--channel", required=True, help="e.g. latest-stable")
    p.add_argument("--variant", default="", help="optional: edition=full,...")
    p.add_argument("--signing-pubkey", default="")
    p.add_argument("-o", "--output", type=Path, default=None, help="write to file (default stdout)")
    args = p.parse_args(argv)

    catalog_bytes = args.catalog.read_bytes()
    catalog = json.loads(catalog_bytes)
    platform = _parse_kv(args.platform)
    variant = _parse_kv(args.variant)
    slice_doc = compile_for_target(
        catalog,
        platform=platform,
        channel=args.channel,
        variant=variant or None,
        signing_pubkey=args.signing_pubkey,
        catalog_bytes=catalog_bytes,
    )
    out_bytes = serialize_slice(slice_doc)
    if args.output:
        args.output.write_bytes(out_bytes)
    else:
        sys.stdout.buffer.write(out_bytes)
    return 0


def gen_schema_main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="manifest-gen-schema")
    p.add_argument("-o", "--output", type=Path, default=None)
    args = p.parse_args(argv)
    schema = generate_json_schema()
    text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def resolve_main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="manifest-resolve",
        description=(
            "Resolve a (tool, platform, channel, variant) query against a "
            "Catalog. Default: one match (pretty JSON, exit 1 on ambiguity). "
            "With --all: every match as JSON Lines, ordered most-preferred "
            "first (exit 0 even when empty)."
        ),
    )
    p.add_argument("catalog", type=Path)
    p.add_argument("--tool", required=True)
    p.add_argument("--platform", required=True, help="os=linux,arch=x86_64")
    p.add_argument("--channel", required=True)
    p.add_argument("--variant", default="")
    p.add_argument(
        "--all",
        action="store_true",
        help=(
            "Return every matching asset as JSON Lines, deduped and "
            "ordered by arch-expansion priority then specificity. Use "
            "this when the query is ambiguous (e.g. arch=x86 matches "
            "both x86_64 and i686 builds) and you want to see every "
            "candidate instead of getting an AmbiguityError."
        ),
    )
    args = p.parse_args(argv)
    catalog = _read_json(args.catalog)
    plat = _parse_kv(args.platform)
    var = _parse_kv(args.variant) or None

    if args.all:
        try:
            results = resolve_all_in_catalog(catalog, args.tool, plat, args.channel, var)
        except ResolveError as exc:
            print(f"resolve failed: {exc}", file=sys.stderr)
            return 1
        for r in results:
            # JSON Lines: one compact JSON doc per line, no trailing comma.
            sys.stdout.write(json.dumps(r.asset, sort_keys=True) + "\n")
        return 0

    try:
        asset = resolve_in_catalog(catalog, args.tool, plat, args.channel, var)
    except ResolveError as exc:
        print(f"resolve failed: {exc}", file=sys.stderr)
        return 1
    json.dump(asset, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


__all__ = [
    "compile_main",
    "gen_schema_main",
    "resolve_main",
    "validate_main",
]
