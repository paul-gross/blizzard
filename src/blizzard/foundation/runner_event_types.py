"""The runner's SSE frame-kind vocabulary — one definition, shared by the broker that publishes
a frame and the wire that describes it."""

from __future__ import annotations

from enum import StrEnum


class RunnerEventType(StrEnum):
    """Every runner SSE frame kind — the ``event:`` name a frame carries."""

    LEASE_CHANGED = "lease-changed"
    ASK_CHANGED = "ask-changed"
    ESCALATION_CHANGED = "escalation-changed"
    TAKEOVER_CHANGED = "takeover-changed"
    ENVIRONMENT_CHANGED = "environment-changed"
    FACT_CHANGED = "fact-changed"
