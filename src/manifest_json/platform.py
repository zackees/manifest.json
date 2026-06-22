"""Platform tuple <-> flat-string serialization for filesystem paths.

When a producer hosts binaries themselves (the apple-sdk pattern), the
filesystem layout must encode the platform tuple as a single directory
name. The canonical form is:

    <os>-<arch>[-<libc-or-abi>]

Examples (from real soldr-toolchain assets):

    darwin-universal2          -> {os: darwin,  arch: universal2}
    darwin-aarch64             -> {os: darwin,  arch: aarch64}
    linux-x86_64-musl          -> {os: linux,   arch: x86_64, libc: musl}
    linux-x86_64-glibc         -> {os: linux,   arch: x86_64, libc: glibc}
    windows-x86_64-msvc        -> {os: windows, arch: x86_64, abi: msvc}
    windows-x86_64-gnullvm     -> {os: windows, arch: x86_64, abi: gnullvm}

Rules:
  - `os` and `arch` are always present
  - The third segment (if any) is `libc` when set, else `abi`
  - Producer side uses canonical values from DESIGN.md §3.1
  - Filesystem paths are case-sensitive — lowercase everything

The flat form is deliberately NOT round-trippable in all cases —
e.g. a flat `linux-x86_64-musl` doesn't say whether the producer
intended musl-as-libc or musl-as-abi. Producers should use the
typed v1 catalog as the source of truth; the flat string is a
filesystem-only convenience.
"""

from __future__ import annotations

from typing import Any


def flatten_platform(platform: dict[str, Any]) -> str:
    """Serialize a v1 Platform tuple to its canonical flat string for
    filesystem paths. Raises ValueError if `os` or `arch` is missing
    — those are the load-bearing fields and we don't make them
    optional in path encoding.
    """
    if not platform:
        raise ValueError("flatten_platform: empty platform")
    os_name = (platform.get("os") or "").lower()
    arch = (platform.get("arch") or "").lower()
    if not os_name:
        raise ValueError(f"flatten_platform: missing 'os' in {platform!r}")
    if not arch:
        raise ValueError(f"flatten_platform: missing 'arch' in {platform!r}")
    parts = [os_name, arch]
    # libc preferred over abi when both present (libc is OS-level,
    # abi is compiler/linker — libc is the more durable distinction).
    extra = (platform.get("libc") or "").lower()
    if not extra:
        extra = (platform.get("abi") or "").lower()
    if extra:
        parts.append(extra)
    return "-".join(parts)


__all__ = ["flatten_platform"]
