"""Why a runner's walk over claim order passed a chunk — the next-claim explanation's response vocabulary."""

from __future__ import annotations

from enum import StrEnum


class ClaimPassReason(StrEnum):
    """Why a runner's walk passed a chunk: it carries a blocked marking, or the runner cannot run its current node."""

    BLOCKED = "blocked"
    INELIGIBLE = "ineligible"
