"""OpenCode 2's own ``--version`` normalizer and admitted range; the 1.x binding keeps its own."""

from __future__ import annotations

import re

from packaging.specifiers import SpecifierSet

from blizzard.runner.harness.harness_shared import SEMVER_VERSION_GROUP

#: Display form of the admitted range; membership goes through ``admission.version_admitted``.
ADMITTED_OPENCODE_V2_RANGE_TEXT = ">=2.0,<3.0"
ADMITTED_OPENCODE_V2_RANGE = SpecifierSet(ADMITTED_OPENCODE_V2_RANGE_TEXT)

_VERSION_LINE = re.compile(r"^\s*(?:opencode\s+)?v?" + SEMVER_VERSION_GROUP + r"\s*$", re.IGNORECASE)


def normalize_opencode_v2_version(raw: str | None) -> str | None:
    """The bare semantic version in one raw ``opencode --version`` output (``opencode v2.1.4``), or ``None`` when it
    is not exactly one matching line."""
    if raw is None:
        return None
    lines = [line for line in raw.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    match = _VERSION_LINE.fullmatch(lines[0])
    return match.group("version") if match else None


__all__ = ["ADMITTED_OPENCODE_V2_RANGE", "ADMITTED_OPENCODE_V2_RANGE_TEXT", "normalize_opencode_v2_version"]
