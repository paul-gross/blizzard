"""The event-log kind vocabulary both daemons author against — one closed set, so a bare
literal at an authoring site fails typecheck rather than escaping the domain table that
declares it (``blizzard-context:/domain/operations.md`` §Event kinds)."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Literal, cast, get_args

EventLogKind = Literal[
    "needs-human",
    "worker-lost",
    "owner-unresolvable",
    "no-acceptable-harness",
    "hub-node-unroutable-outcome",
    "hub-action-overran",
    "turn-interrupted",
    "flow-landed",
    "hub-action-missing",
    "repository-unresolved",
    "repository-not-linked",
    "forge-capability-missing",
    "forge-budget-low",
    "forge-rate-limited",
    "config-old-field-used",
    "attempt-failed",
    "command-failed",
    "work-item-close-failed",
    "work-item-close-skipped",
    "transcript-truncated",
    "transcript-sidechain-dropped",
    "worker-context-warned",
    "attempt-abandoned",
    "work-item-closed",
    "trace-export-failed",
    "trace-export-recovered",
    "trace-window-skipped",
    "trace-config-rejected",
    "egress-write-failed",
    "egress-write-recovered",
    "egress-config-rejected",
    "egress-cursor-reset",
    "forge-status-source-skipped",
]


class EventLogSeverity(StrEnum):
    """The closed severity vocabulary."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


#: Each kind emits at exactly one severity — a function of kind, never paired independently.
EVENT_LOG_SEVERITY: Mapping[EventLogKind, EventLogSeverity] = {
    "needs-human": EventLogSeverity.CRITICAL,
    "worker-lost": EventLogSeverity.CRITICAL,
    "owner-unresolvable": EventLogSeverity.CRITICAL,
    "no-acceptable-harness": EventLogSeverity.CRITICAL,
    "hub-node-unroutable-outcome": EventLogSeverity.CRITICAL,
    "hub-action-overran": EventLogSeverity.WARNING,
    "turn-interrupted": EventLogSeverity.WARNING,
    "flow-landed": EventLogSeverity.INFO,
    "hub-action-missing": EventLogSeverity.CRITICAL,
    "repository-unresolved": EventLogSeverity.CRITICAL,
    "repository-not-linked": EventLogSeverity.CRITICAL,
    "forge-capability-missing": EventLogSeverity.WARNING,
    "forge-budget-low": EventLogSeverity.WARNING,
    "forge-rate-limited": EventLogSeverity.WARNING,
    "config-old-field-used": EventLogSeverity.WARNING,
    "attempt-failed": EventLogSeverity.WARNING,
    "command-failed": EventLogSeverity.WARNING,
    "work-item-close-failed": EventLogSeverity.WARNING,
    "work-item-close-skipped": EventLogSeverity.WARNING,
    "transcript-truncated": EventLogSeverity.WARNING,
    "transcript-sidechain-dropped": EventLogSeverity.WARNING,
    "worker-context-warned": EventLogSeverity.WARNING,
    "attempt-abandoned": EventLogSeverity.INFO,
    "work-item-closed": EventLogSeverity.INFO,
    "trace-export-failed": EventLogSeverity.WARNING,
    "trace-export-recovered": EventLogSeverity.INFO,
    "trace-window-skipped": EventLogSeverity.WARNING,
    "trace-config-rejected": EventLogSeverity.WARNING,
    "egress-write-failed": EventLogSeverity.WARNING,
    "egress-write-recovered": EventLogSeverity.INFO,
    "egress-config-rejected": EventLogSeverity.WARNING,
    "egress-cursor-reset": EventLogSeverity.INFO,
    "forge-status-source-skipped": EventLogSeverity.WARNING,
}

_EVENT_LOG_KINDS = frozenset(get_args(EventLogKind))


def narrow_event_log_kind(value: str) -> EventLogKind | None:
    """``value`` narrowed to :data:`EventLogKind`, or ``None`` outside the closed vocabulary."""
    return cast(EventLogKind, value) if value in _EVENT_LOG_KINDS else None


def narrow_event_log_severity(value: str) -> EventLogSeverity | None:
    """``value`` narrowed to :data:`EventLogSeverity`, or ``None`` outside the closed vocabulary."""
    try:
        return EventLogSeverity(value)
    except ValueError:
        return None
