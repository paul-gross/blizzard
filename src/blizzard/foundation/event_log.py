"""The event-log kind vocabulary both daemons author against — one closed set, so a bare
literal at an authoring site fails typecheck rather than escaping the domain table that
declares it (``blizzard-context:/domain/operations.md`` §Event kinds)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

EventLogKind = Literal[
    "needs-human",
    "worker-lost",
    "hub-node-unroutable-outcome",
    "attempt-failed",
    "command-failed",
    "work-item-close-failed",
    "transcript-truncated",
    "transcript-sidechain-dropped",
    "attempt-abandoned",
    "work-item-closed",
]

#: Each kind emits at exactly one severity — a function of kind, never paired independently.
EVENT_LOG_SEVERITY: Mapping[EventLogKind, str] = {
    "needs-human": "critical",
    "worker-lost": "critical",
    "hub-node-unroutable-outcome": "critical",
    "attempt-failed": "warning",
    "command-failed": "warning",
    "work-item-close-failed": "warning",
    "transcript-truncated": "warning",
    "transcript-sidechain-dropped": "warning",
    "attempt-abandoned": "info",
    "work-item-closed": "info",
}
