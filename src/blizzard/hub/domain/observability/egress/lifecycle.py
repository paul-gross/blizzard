"""The fact export's states, and which operator and sweep verbs are legal from each.

A dataset's cursor is unanchored before its first pass and anchored after; the export as a whole
is off when no directory is configured or its settings are rejected, and nothing is wired then."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class ExportState(StrEnum):
    OFF = "off"
    UNANCHORED = "unanchored"
    ANCHORED = "anchored"


class ExportVerb(StrEnum):
    SWEEP = "sweep"
    RESET = "reset"
    BACKFILL = "backfill"
    DRY_BACKFILL = "dry_backfill"
    STATUS = "status"


_EVERY_VERB = frozenset(ExportVerb)

#: The verbs legal from each state; while off, only a dry backfill and the status read run.
EGRESS_TRANSITIONS: Mapping[ExportState, frozenset[ExportVerb]] = MappingProxyType(
    {
        ExportState.OFF: frozenset({ExportVerb.DRY_BACKFILL, ExportVerb.STATUS}),
        ExportState.UNANCHORED: _EVERY_VERB,
        ExportState.ANCHORED: _EVERY_VERB,
    }
)


def export_allows(verb: ExportVerb, *, wired: bool) -> bool:
    """Whether ``verb`` may run: from any state while the export is wired, else from ``off``."""
    return wired or verb in EGRESS_TRANSITIONS[ExportState.OFF]
