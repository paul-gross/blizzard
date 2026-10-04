"""The fleet trace export's states, and which sweep and operator verbs are legal from each.

The export is off when tracing is not configured or its settings are rejected — no exporter is
wired then — and enabled otherwise."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class TraceExportState(StrEnum):
    OFF = "off"
    ENABLED = "enabled"


class TraceVerb(StrEnum):
    SWEEP = "sweep"
    REPLAY = "replay"
    DRY_REPLAY = "dry_replay"
    STATUS = "status"


#: The verbs legal from each state; while off, only a dry replay and the status read run.
TRACE_EXPORT_TRANSITIONS: Mapping[TraceExportState, frozenset[TraceVerb]] = MappingProxyType(
    {
        TraceExportState.OFF: frozenset({TraceVerb.DRY_REPLAY, TraceVerb.STATUS}),
        TraceExportState.ENABLED: frozenset(TraceVerb),
    }
)


def trace_export_allows(verb: TraceVerb, *, exporter_wired: bool) -> bool:
    """Whether ``verb`` may run in the state the wiring puts the export in."""
    state = TraceExportState.ENABLED if exporter_wired else TraceExportState.OFF
    return verb in TRACE_EXPORT_TRANSITIONS[state]
