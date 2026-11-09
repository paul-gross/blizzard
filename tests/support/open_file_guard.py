"""Inspect test-owned open files at scope and worker boundaries on Linux."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


def inspect(root: Path | None, sqlite_paths: set[Path] | None = None) -> list[str]:
    """Find deleted regular files below root and surviving tracked SQLite files."""
    if not sys.platform.startswith("linux"):
        return []
    root = root.resolve() if root is not None else None
    sqlite_paths = sqlite_paths or set()
    findings: list[str] = []
    for fd in Path("/proc/self/fd").iterdir():
        try:
            target = os.readlink(fd)
            mode = fd.stat().st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(mode):
            continue
        deleted = target.endswith(" (deleted)")
        path = Path(target.removesuffix(" (deleted)"))
        if (deleted and root is not None and path.is_relative_to(root)) or path in sqlite_paths:
            findings.append(f"fd={fd.name} target={target}")
    return sorted(findings)


def describe(scope: str, findings: list[str]) -> str:
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    count = str(len(findings)) if sys.platform.startswith("linux") else "unsupported"
    return f"open-file guard: scope={scope} worker={worker} pid={os.getpid()} count={count}\n" + "\n".join(findings)
