"""The event-log kind vocabulary both daemons author against — one closed set, so a bare
literal at an authoring site fails typecheck rather than escaping the domain table that
declares it (``blizzard-context:/domain/operations.md`` §Event kinds)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast, get_args

EventLogKind = Literal[
    "needs-human",
    "worker-lost",
    "owner-unresolvable",
    "no-acceptable-harness",
    "hub-node-unroutable-outcome",
    "attempt-failed",
    "command-failed",
    "work-item-close-failed",
    "transcript-truncated",
    "transcript-sidechain-dropped",
    "worker-context-warned",
    "attempt-abandoned",
    "work-item-closed",
    "trace-export-failed",
    "trace-export-recovered",
    "trace-window-skipped",
    "trace-config-rejected",
]

#: The closed severity vocabulary; every wire severity field narrows against it.
EventLogSeverity = Literal["critical", "warning", "info"]

#: Each kind emits at exactly one severity — a function of kind, never paired independently.
EVENT_LOG_SEVERITY: Mapping[EventLogKind, EventLogSeverity] = {
    "needs-human": "critical",
    "worker-lost": "critical",
    "owner-unresolvable": "critical",
    "no-acceptable-harness": "critical",
    "hub-node-unroutable-outcome": "critical",
    "attempt-failed": "warning",
    "command-failed": "warning",
    "work-item-close-failed": "warning",
    "transcript-truncated": "warning",
    "transcript-sidechain-dropped": "warning",
    "worker-context-warned": "warning",
    "attempt-abandoned": "info",
    "work-item-closed": "info",
    "trace-export-failed": "warning",
    "trace-export-recovered": "info",
    "trace-window-skipped": "warning",
    "trace-config-rejected": "warning",
}

_EVENT_LOG_KINDS = frozenset(get_args(EventLogKind))
_EVENT_LOG_SEVERITIES = frozenset(get_args(EventLogSeverity))


def narrow_event_log_kind(value: str) -> EventLogKind | None:
    """``value`` narrowed to :data:`EventLogKind`, or ``None`` outside the closed vocabulary."""
    return cast(EventLogKind, value) if value in _EVENT_LOG_KINDS else None


def narrow_event_log_severity(value: str) -> EventLogSeverity | None:
    """``value`` narrowed to :data:`EventLogSeverity`, or ``None`` outside the closed vocabulary."""
    return cast(EventLogSeverity, value) if value in _EVENT_LOG_SEVERITIES else None
