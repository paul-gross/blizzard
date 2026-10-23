"""The release's rule (unit tier) — :func:`refuse_release` over loaded values: who holds the route,
which status the verb is legal from, and which epoch is the latest. No repository, no clock."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.chunk.model import (
    CHUNK_VERB_LEGALITY,
    ChunkFacts,
    ChunkVerb,
    EpochOwnerFact,
    PauseFact,
    QuestionFact,
    RouteCreatedFact,
)
from blizzard.hub.domain.execution.release import ReleaseRefusalKind, refuse_release
from blizzard.hub.domain.runners.route import Route

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_ROUTE = Route(chunk_id="chk_1", runner_id="rn_1", workspace_id="ws_1", environment_ids=["env_1"], created_at=_T0)


def _running(
    *, epoch: int = 1, pauses: list[PauseFact] | None = None, questions: list[QuestionFact] | None = None
) -> ChunkFacts:
    return ChunkFacts(
        minted=True,
        promoted=True,
        routes_created=[RouteCreatedFact(created_at=_T0)],
        epoch_owners=[EpochOwnerFact(epoch=epoch, runner_id="rn_1")],
        pauses=pauses or [],
        questions=questions or [],
    )


def test_release_is_legal_from_running_alone() -> None:
    assert CHUNK_VERB_LEGALITY[ChunkVerb.RELEASE] == frozenset({ChunkStatus.RUNNING})


def test_the_holder_at_the_latest_epoch_is_admitted() -> None:
    assert refuse_release(_ROUTE, _running(), runner_id="rn_1", epoch=1) is None


def test_a_caller_not_holding_the_route_is_refused() -> None:
    assert refuse_release(_ROUTE, _running(), runner_id="rn_2", epoch=1) is ReleaseRefusalKind.NOT_HOLDER


def test_a_chunk_with_no_live_route_is_refused_as_not_held() -> None:
    assert refuse_release(None, _running(), runner_id="rn_1", epoch=1) is ReleaseRefusalKind.NOT_HOLDER


@pytest.mark.parametrize(
    "facts",
    [
        _running(pauses=[PauseFact(paused=True, set_at=_T0, set_by="op")]),
        _running(questions=[QuestionFact("q1", _T0)]),
    ],
    ids=["paused", "waiting-on-human"],
)
def test_a_held_chunk_not_running_is_refused(facts: ChunkFacts) -> None:
    assert facts.status() is not ChunkStatus.RUNNING

    assert refuse_release(_ROUTE, facts, runner_id="rn_1", epoch=1) is ReleaseRefusalKind.NOT_RUNNING


@pytest.mark.parametrize("epoch", [0, 2])
def test_an_epoch_other_than_the_latest_is_stale(epoch: int) -> None:
    assert refuse_release(_ROUTE, _running(epoch=1), runner_id="rn_1", epoch=epoch) is ReleaseRefusalKind.STALE
