"""Why an escalation happened; the wire carries it as an open string."""

from __future__ import annotations

from enum import StrEnum


class EscalationCause(StrEnum):
    RETRIES_EXHAUSTED = "retries-exhausted"
    OWNER_UNRESOLVABLE = "owner-unresolvable"
    NO_ACCEPTABLE_HARNESS = "no-acceptable-harness"
    SPEND_CAP = "spend-cap"
    BOUNCE_CAP = "bounce-cap"
    MIGRATION_TARGET_UNRESOLVABLE = "migration-target-unresolvable"
    HUB_ACTION_MISSING = "hub-action-missing"
    HUB_ACTION_STUCK = "hub-action-stuck"
    UNROUTABLE_OUTCOME = "unroutable-outcome"
    FORGE_AUTH = "forge-auth"
    FORGE_CONFIG = "forge-config"
    FORGE_CAPABILITY_MISSING = "forge-capability-missing"
    ESCALATE_CHOICE = "escalate-choice"
