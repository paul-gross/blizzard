"""The prerequisite-met and chunk-landed rules, each with one home on its model, pinned by value —
no repository, no clock."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.chunk.dependencies import derive_blocked_prerequisites
from blizzard.hub.domain.chunk.model import ChunkFacts, DependencyEdge
from blizzard.hub.domain.execution.claim import first_unmet_prerequisite

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _edge(prerequisite: str, dependent: str = "chk_dep") -> DependencyEdge:
    return DependencyEdge(
        dependency_id=f"dep_{prerequisite}",
        dependent_chunk_id=dependent,
        prerequisite_chunk_id=prerequisite,
        declared_at=_T0,
        declared_by="op",
    )


@pytest.mark.parametrize("status", list(ChunkStatus))
def test_only_a_done_prerequisite_meets_its_edge(status: ChunkStatus) -> None:
    assert DependencyEdge.met_by(status) is (status is ChunkStatus.DONE)


def test_an_absent_prerequisite_does_not_meet_its_edge() -> None:
    assert DependencyEdge.met_by(None) is False


def test_claim_and_the_blocked_marking_agree_on_which_prerequisites_are_unmet() -> None:
    done = ChunkFacts(minted=True, operator_completed=True, operator_completed_at=_T0)
    stopped = ChunkFacts(minted=True, stopped=True, stopped_at=_T0)
    edges = [_edge("chk_done"), _edge("chk_stopped"), _edge("chk_absent")]
    facts = {"chk_done": done, "chk_stopped": stopped}

    blocked = derive_blocked_prerequisites(edges, {cid: f.status() for cid, f in facts.items()})

    assert blocked == {"chk_dep": ["chk_stopped", "chk_absent"]}
    assert first_unmet_prerequisite("chk_dep", edges, facts) == "chk_stopped"


def test_a_chunk_reads_landed_from_a_named_repo_or_a_recorded_landing() -> None:
    bare = ChunkFacts(minted=True)
    assert bare.landed_with([]) is False
    assert bare.landed_with(["acme/widget"]) is True
    assert ChunkFacts(minted=True, delivery_landed=True).landed_with([]) is True
    assert ChunkFacts(minted=True, landed_repos=frozenset({"acme/widget"})).landed_with([]) is True
