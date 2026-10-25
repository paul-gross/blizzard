"""``blizzard-compat`` read through its command line — the one place other tools run it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

BREAKS_FILE = "compat-breaks.json"


class CompatError(RuntimeError):
    """``blizzard-compat`` did not run, or did not yield what was asked of it."""


class SupportedReleasesError(CompatError):
    """``blizzard-compat releases --json`` did not yield the supported releases."""


def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=300, cwd=cwd)
    except (OSError, subprocess.SubprocessError) as error:
        raise CompatError(f"{' '.join(command)} could not run: {error}") from error


def supported_tags(tag: str | None = None) -> frozenset[str]:
    """The supported release tags, read through ``blizzard-compat`` rather than recomputed here.

    ``tag`` is a version whose own tag the listing leaves out, as a run judging that candidate does."""
    command = ["blizzard-compat", "releases", "--json", *(["--tag", tag] if tag is not None else [])]
    try:
        result = _run(command)
    except CompatError as error:
        raise SupportedReleasesError(str(error)) from error
    if result.returncode != 0:
        raise SupportedReleasesError(
            f"blizzard-compat releases --json failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        return frozenset(release["tag"] for release in json.loads(result.stdout)["releases"])
    except (ValueError, KeyError, TypeError) as error:
        raise SupportedReleasesError(f"blizzard-compat releases --json gave an unreadable document: {error}") from error


def tag_breaks(root: Path, version: str) -> str:
    """Run ``blizzard-compat --tag <version>`` at ``root`` and return the ``compat-breaks.json`` text it wrote.

    A file from an earlier run is removed first, so it cannot pass for this run's."""
    path = root / BREAKS_FILE
    path.unlink(missing_ok=True)
    result = _run(["blizzard-compat", "--tag", version], cwd=root)
    if result.returncode != 0:
        raise CompatError(f"blizzard-compat --tag {version} failed:\n{(result.stdout + result.stderr).strip()}")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise CompatError(f"blizzard-compat --tag {version} left no readable {BREAKS_FILE}: {error}") from error
