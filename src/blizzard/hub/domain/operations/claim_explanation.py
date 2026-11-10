"""One runner's next claim, explained — the matched peek's own walk with its trace kept.

The explanation composes :func:`~blizzard.hub.domain.operations.queue.walk_matched_entries` and never judges a chunk
itself, so its ``next`` is the pass-over peek's choice by construction. The hub holds no record of a runner's
hold/pass-over policy, so it answers both: ``next`` and ``passed`` from a pass-over walk, ``if_holding`` from a
per-project hold walk, and ``held_at`` from a hold over each served project alone."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from blizzard.foundation.queue_policy import QueueMatchPolicy
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts
from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.domain.operations.queue import MatchedEntry, MatchedWalk, PassedEntry, walk_matched_entries
from blizzard.hub.domain.runners.registration import RunnerCapability

#: The most passed chunks an explanation lists; ``passed_total`` still counts every one.
PASSED_CAP = 20


@domain_model
@dataclass(frozen=True)
class ClaimExplanation:
    """What a runner reaches for next and why it passes what it passes.

    ``next`` is the pass-over choice and ``passed`` the chunks it passed first, in claim order, at most
    :data:`PASSED_CAP`, with ``passed_total`` the full count. ``unserved`` counts the chunks ahead of ``next`` the walk
    never examined, their project outside the served set. ``if_holding`` is the per-project hold walk's choice, and
    ``held_at`` the unusable head each served project's hold stops at, in claim order."""

    next: MatchedEntry | None
    passed: tuple[PassedEntry, ...]
    passed_total: int
    unserved: int
    if_holding: MatchedEntry | None
    held_at: tuple[PassedEntry, ...]


def explain_next_claim(
    chunks: Sequence[Chunk],
    *,
    served: Collection[str],
    graphs: Mapping[str, Graph],
    facts: Mapping[str, ChunkFacts],
    capabilities: Sequence[RunnerCapability],
    blocked: Mapping[str, list[str]],
) -> ClaimExplanation:
    """The explanation over ``chunks`` in claim order, judged by the walk the matched peek takes."""

    def walk(policy: QueueMatchPolicy, projects: Collection[str], *, per_project: bool = False) -> MatchedWalk:
        return walk_matched_entries(
            chunks,
            served=projects,
            graphs=graphs,
            facts=facts,
            capabilities=capabilities,
            blocked=blocked,
            policy=policy,
            hold_per_project=per_project,
        )

    pass_over = walk(QueueMatchPolicy.PASS_OVER, served)
    holding = walk(QueueMatchPolicy.HOLD, served, per_project=True)
    heads = dict.fromkeys(chunk.project_id for chunk in chunks if chunk.project_id in served)
    held_at = tuple(
        entry
        for project_id in heads
        if project_id is not None
        for entry in walk(QueueMatchPolicy.HOLD, {project_id}).passed
    )
    return ClaimExplanation(
        next=pass_over.choice,
        passed=pass_over.passed[:PASSED_CAP],
        passed_total=len(pass_over.passed),
        unserved=pass_over.unserved,
        if_holding=holding.choice,
        held_at=held_at,
    )
