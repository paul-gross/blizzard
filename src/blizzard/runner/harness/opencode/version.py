"""OpenCode's own ``--version`` normalizer. Each binding owns its raw shape and normalizer."""

from __future__ import annotations

import re

from blizzard.runner.harness.harness_shared import SEMVER_VERSION_GROUP

# Strips a leading `opencode`/"version"/"v" prefix off one line of OpenCode's `--version` output.
OPENCODE_VERSION_PATTERN = re.compile(
    r"^\s*(?:opencode(?:\s+version)?\s+)?(?:v)?" + SEMVER_VERSION_GROUP + r"\s*$",
    re.IGNORECASE,
)


def normalize_opencode_version(raw: str | None) -> str | None:
    """The bare semantic version in one raw OpenCode ``--version`` output, or ``None`` when it
    isn't exactly one matching line. Scoped to OpenCode alone."""
    if raw is None:
        return None
    lines = [line for line in raw.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    match = OPENCODE_VERSION_PATTERN.fullmatch(lines[0])
    return match.group("version") if match else None
