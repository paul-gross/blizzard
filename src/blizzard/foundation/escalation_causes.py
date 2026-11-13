"""Why an escalation happened; the wire carries it as an open string."""

from __future__ import annotations

from enum import StrEnum


class EscalationCause(StrEnum):
    RETRIES_EXHAUSTED = "retries-exhausted"
    OWNER_UNRESOLVABLE = "owner-unresolvable"
    NO_ACCEPTABLE_HARNESS = "no-acceptable-harness"
    HARNESS_VERSION_CHANGED = "harness-version-changed"
    SPEND_CAP = "spend-cap"
    BOUNCE_CAP = "bounce-cap"
    MIGRATION_TARGET_UNRESOLVABLE = "migration-target-unresolvable"
    HUB_ACTION_MISSING = "hub-action-missing"
    HUB_ACTION_STUCK = "hub-action-stuck"
    UNROUTABLE_OUTCOME = "unroutable-outcome"
    FORGE_AUTH = "forge-auth"
    FORGE_CONFIG = "forge-config"
    FORGE_CAPABILITY_MISSING = "forge-capability-missing"
    CHANGE_CLOSED = "change-closed"
    BRANCH_PROTECTED = "branch-protected"
    MERGE_METHOD_REFUSED = "merge-method-refused"
    MISSING_CHECKS = "missing-checks"
    PARK_TIMEOUT = "park-timeout"
    ESCALATE_CHOICE = "escalate-choice"
