from __future__ import annotations

from enum import StrEnum

# The authored-tier prefix — unprefixed is a harness-native name, never guessed.
TIER_PREFIX = "blizzard:"


class Executor(StrEnum):
    """Where a node's step runs."""

    RUNNER = "runner"
    HUB = "hub"


class JudgedBy(StrEnum):
    """Who issues a node's exit judgement — the structural gate marker."""

    WORKER = "worker"
    HUMAN = "human"


class SessionMode(StrEnum):
    """Per-node session freshness."""

    RESUME = "resume"
    FRESH = "fresh"


class ApplyOutcome(StrEnum):
    """What a completion's apply produced."""

    NEXT = "next"  # the runner continues in place; `next_envelope` is set
    HUB_NODE_TAKEN = "hub_node_taken"  # a hub node (deliver) took over; runner holds envs, waits
    PARKED_AT_GATE = "parked_at_gate"  # a human gate: waiting_on_human (shaped, P7)
    MIGRATED = "migrated"  # a cross-graph migration re-pinned + re-queued the chunk (#90); runner tears down
    DONE = "done"  # the chunk reached the terminal
    FAILURE = "failure"  # stale epoch, terminal chunk, or a rejected submission
