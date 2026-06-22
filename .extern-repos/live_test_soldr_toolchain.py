#!/usr/bin/env python3
"""Real-world end-to-end test of zackees/soldr-toolchain's live manifest.json.

Walks the federated Index, validates every Catalog, then runs
representative resolve queries (incl. wildcard + source-fallback edge cases).

Expected to hit the Pages URL https://zackees.github.io/soldr-toolchain/.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request

from manifest_json import (
    AmbiguityError,
    ChannelNotFoundError,
    NoBinaryOrSourceError,
    NoMatchingAssetError,
    resolve_in_catalog,
    resolve_or_source,
    validate_document,
    ValidationError,
)

BASE = "https://zackees.github.io/soldr-toolchain/"


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "manifest-json/0.1 live-test"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    failures: list[str] = []

    # 1. Fetch top-level Index, validate it.
    print(f"GET {BASE}manifest.json")
    index = fetch_json(BASE + "manifest.json")
    try:
        validate_document(index)
        print(f"  Index validates: {len(index['tools'])} tools")
    except ValidationError as e:
        failures.append(f"Index does not validate: {e}")
        print(f"  FAIL: {e}")
        return 1

    # 2. Fetch every per-tool Catalog via the Index descriptors. Validate each.
    catalogs: dict[str, dict] = {}
    for tool_name, entry in sorted(index["tools"].items()):
        rel_url = entry["descriptor"]["url"]
        full_url = urllib.parse.urljoin(BASE, rel_url)
        try:
            catalog = fetch_json(full_url)
        except Exception as e:
            failures.append(f"{tool_name}: cannot fetch {full_url}: {e}")
            print(f"  FAIL fetch {tool_name}: {e}")
            continue
        try:
            validate_document(catalog)
        except ValidationError as e:
            failures.append(f"{tool_name}: catalog does not validate: {e}")
            print(f"  FAIL validate {tool_name}: {e}")
            continue
        catalogs[tool_name] = catalog
        print(f"  OK {tool_name}: {len(catalog.get('releases', []))} releases, "
              f"channels={sorted((catalog.get('channels') or {}).keys())}")

    # 3. Resolve queries — table-driven, covers happy path and edge cases.
    queries = [
        ("happy: zccache linux-x86_64-musl latest-stable",
         "zccache", {"os": "linux", "arch": "x86_64", "libc": "musl"}, "latest-stable", None, "ok"),
        ("happy: zccache windows-x86_64-msvc latest-stable",
         "zccache", {"os": "windows", "arch": "x86_64", "abi": "msvc"}, "latest-stable", None, "ok"),
        ("happy: zccache darwin-aarch64 latest-stable",
         "zccache", {"os": "darwin", "arch": "aarch64"}, "latest-stable", None, "ok"),
        ("happy: cargo-chef linux-musl pinned",
         "cargo-chef", {"os": "linux", "arch": "x86_64", "libc": "musl"}, "pinned", None, "ok"),
        ("happy: apple-sdk darwin-aarch64 latest-stable",
         "apple-sdk", {"os": "darwin", "arch": "aarch64"}, "latest-stable", None, "ok"),
        ("happy: crgx linux-musl latest-stable (post-fix)",
         "crgx", {"os": "linux", "arch": "x86_64", "libc": "musl"}, "latest-stable", None, "ok"),
        ("happy: crgx linux-glibc latest-stable",
         "crgx", {"os": "linux", "arch": "x86_64", "libc": "glibc"}, "latest-stable", None, "ok"),
        # cargo-chef ships BOTH glibc and musl on linux-x86_64, so a query
        # with no libc constraint is genuinely ambiguous. zccache ships
        # only musl on linux, so its wildcard query would be unique.
        ("edge: cargo-chef linux-x86_64 (no libc) -> Ambiguity expected",
         "cargo-chef", {"os": "linux", "arch": "x86_64"}, "latest-stable", None, "ambiguity"),
        ("edge: zccache unknown channel -> ChannelNotFound expected",
         "zccache", {"os": "linux", "arch": "x86_64", "libc": "musl"}, "bogus", None, "channel"),
        ("edge: zccache haiku -> NoMatchingAsset expected",
         "zccache", {"os": "haiku", "arch": "x86_64"}, "latest-stable", None, "nomatch"),
        ("edge: zccache haiku -> source fallback (resolve_or_source)",
         "zccache", {"os": "haiku", "arch": "x86_64"}, "latest-stable", None, "source"),
    ]

    for label, tool, plat, chan, variant, expect in queries:
        catalog = catalogs.get(tool)
        if catalog is None:
            failures.append(f"{label}: tool {tool!r} not in catalogs")
            print(f"  SKIP {label}: tool not loaded")
            continue
        try:
            if expect == "source":
                r = resolve_or_source(catalog, tool, plat, chan, variant)
                if r.kind != "source":
                    failures.append(f"{label}: expected source fallback, got {r.kind}")
                    print(f"  FAIL {label}: kind={r.kind}")
                else:
                    print(f"  OK   {label}: source={r.source['repo_url']}")
            else:
                asset = resolve_in_catalog(catalog, tool, plat, chan, variant)
                if expect != "ok":
                    failures.append(f"{label}: expected {expect}, got asset {asset['filename']}")
                    print(f"  FAIL {label}: unexpected success")
                else:
                    print(f"  OK   {label}: {asset['filename']}")
        except AmbiguityError as e:
            if expect == "ambiguity":
                print(f"  OK   {label}: Ambiguity ({len(e.candidates)} candidates)")
            else:
                failures.append(f"{label}: unexpected Ambiguity ({len(e.candidates)})")
                print(f"  FAIL {label}: unexpected Ambiguity")
        except ChannelNotFoundError as e:
            if expect == "channel":
                print(f"  OK   {label}: ChannelNotFound")
            else:
                failures.append(f"{label}: unexpected ChannelNotFound: {e}")
                print(f"  FAIL {label}: {e}")
        except NoMatchingAssetError as e:
            if expect == "nomatch":
                print(f"  OK   {label}: NoMatchingAsset")
            else:
                failures.append(f"{label}: unexpected NoMatchingAsset: {e}")
                print(f"  FAIL {label}: {e}")
        except Exception as e:
            failures.append(f"{label}: {type(e).__name__}: {e}")
            print(f"  FAIL {label}: {type(e).__name__}: {e}")

    # 4. Verify that asset URLs are real (HEAD just one as a smoke check).
    catalog = catalogs.get("zccache")
    if catalog is not None:
        asset = resolve_in_catalog(catalog, "zccache",
            {"os": "linux", "arch": "x86_64", "libc": "musl"}, "latest-stable")
        url = asset["urls"][0]
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "live-test"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                print(f"  HEAD {url} -> {resp.status}")
        except Exception as e:
            failures.append(f"HEAD failed for {url}: {e}")
            print(f"  FAIL HEAD {url}: {e}")

    print()
    if failures:
        print(f"=== {len(failures)} FAILURES ===")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("=== ALL PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
