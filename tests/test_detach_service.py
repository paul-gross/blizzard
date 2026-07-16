"""DetachService (unit tier) — the operator-release write, facts only (D-088).

A fake :class:`IWriteChunkRepository` stands in for the store — only ``route_of`` and
``record_route_released`` are meaningfully implemented; every other seam is
unreachable from :meth:`DetachService.detach` and raises loudly if a regression
starts calling it (``bzh:domain-core`` — no store, no tokens).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.artifacts import ArtifactRow
from blizzard.hub.domain.detach import DetachService, NotRouted
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.work import (
    AnswerOutcome,
    Chunk,
    ChunkFacts,
    DecisionChoice,
    DecisionRow,
    PmPointer,
    PrClosedFact,
    PrOpenedFact,
    QuestionRow,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", pm_pointers=[], minted_at=_T0)


@dataclass
class _FakeChunkRepo:
    """Only ``route_of``/``record_route_released`` are live; anything else is a bug."""

    route: Route | None
    released: list[tuple[str, datetime]] = field(default_factory=list)

    def route_of(self, chunk_id: str) -> Route | None:
        return self.route

    def record_route_released(self, chunk_id: str, *, at: datetime) -> None:
        self.released.append((chunk_id, at))

    def _unexpected(self, *_a: object, **_k: object) -> object:
        raise NotImplementedError("DetachService should not touch this seam")

    def get(self, chunk_id: str) -> Chunk | None:
        return self._unexpected()  # type: ignore[return-value]

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        return self._unexpected()  # type: ignore[return-value]

    def get_question(self, question_id: str) -> QuestionRow | None:
        return self._unexpected()  # type: ignore[return-value]

    def list_open_questions(self) -> list[QuestionRow]:
        return self._unexpected()  # type: ignore[return-value]

    def load_questions(self, chunk_id: str) -> list[QuestionRow]:
        return self._unexpected()  # type: ignore[return-value]

    def load_artifacts(self, chunk_id: str) -> list[ArtifactRow]:
        return self._unexpected()  # type: ignore[return-value]

    def list_ready(self) -> list[Chunk]:
        return self._unexpected()  # type: ignore[return-value]

    def list_all(self) -> list[Chunk]:
        return self._unexpected()  # type: ignore[return-value]

    def queue_positions(self) -> dict[str, float]:
        return self._unexpected()  # type: ignore[return-value]

    def find_live_holder(self, pointer: PmPointer) -> str | None:
        return self._unexpected()  # type: ignore[return-value]

    def accepted_transition_target(self, chunk_id: str, *, from_node_id: str, epoch: int) -> str | None:
        return self._unexpected()  # type: ignore[return-value]

    def landed_repos(self, chunk_id: str) -> set[str]:
        return self._unexpected()  # type: ignore[return-value]

    def open_prs(self, chunk_id: str) -> list[PrOpenedFact]:
        return self._unexpected()  # type: ignore[return-value]

    def runner_high_water(self, runner_id: str) -> int:
        return self._unexpected()  # type: ignore[return-value]

    def get_decision(self, decision_id: str) -> DecisionRow | None:
        return self._unexpected()  # type: ignore[return-value]

    def find_decision(self, chunk_id: str, *, node_id: str, epoch: int) -> DecisionRow | None:
        return self._unexpected()  # type: ignore[return-value]

    def decision_for_chunk(self, chunk_id: str) -> DecisionRow | None:
        return self._unexpected()  # type: ignore[return-value]

    def list_open_decisions(self) -> list[DecisionRow]:
        return self._unexpected()  # type: ignore[return-value]

    def mint(self, chunk: Chunk) -> None:
        self._unexpected()

    def record_promote(self, chunk_id: str, *, at: datetime) -> None:
        self._unexpected()

    def record_lease(self, chunk_id: str, *, epoch: int, runner_id: str, at: datetime) -> None:
        self._unexpected()

    def set_runner_high_water(self, runner_id: str, *, seq: int, at: datetime) -> None:
        self._unexpected()

    def record_route(self, route: Route, *, at: datetime) -> None:
        self._unexpected()

    def record_transition(
        self,
        *,
        transition_id: str,
        chunk_id: str,
        from_node_id: str | None,
        to_node_id: str,
        choice_name: str | None,
        epoch: int,
        runner_id: str,
        at: datetime,
        artifacts: list[ArtifactRow],
        decision_id: str | None = None,
    ) -> None:
        self._unexpected()

    def record_delivery_repo_landed(self, chunk_id: str, *, repo: str, commit_hash: str, at: datetime) -> None:
        self._unexpected()

    def record_delivery_landed(self, chunk_id: str, *, at: datetime) -> None:
        self._unexpected()

    def finalize_delivery(
        self,
        chunk_id: str,
        *,
        from_node_id: str,
        to_node_id: str,
        choice_name: str,
        epoch: int,
        runner_id: str,
        transition_id: str,
        at: datetime,
    ) -> bool:
        return self._unexpected()  # type: ignore[return-value]

    def record_pr_opened(
        self, chunk_id: str, *, repo: str, number: int, url: str, commit_hash: str, at: datetime
    ) -> None:
        self._unexpected()

    def finalize_pr_delivery(
        self,
        chunk_id: str,
        *,
        closed: list[PrClosedFact],
        from_node_id: str,
        to_node_id: str,
        choice_name: str,
        epoch: int,
        runner_id: str,
        transition_id: str,
        at: datetime,
    ) -> bool:
        return self._unexpected()  # type: ignore[return-value]

    def record_escalation(self, chunk_id: str, *, epoch: int, takeover_command: str, at: datetime) -> None:
        self._unexpected()

    def record_question(
        self,
        *,
        question_id: str,
        chunk_id: str,
        node_id: str | None,
        session_id: str | None,
        runner_id: str,
        epoch: int,
        question: str,
        options: list[str],
        asked_at: datetime,
    ) -> None:
        self._unexpected()

    def answer_question(self, question_id: str, *, answer: str, answered_by: str, at: datetime) -> AnswerOutcome:
        return self._unexpected()  # type: ignore[return-value]

    def record_answer_delivered(self, *, question_id: str, chunk_id: str, at: datetime) -> None:
        self._unexpected()

    def record_decision(
        self,
        *,
        decision_id: str,
        chunk_id: str,
        node_id: str,
        node_name: str,
        epoch: int,
        choices: list[DecisionChoice],
        at: datetime,
        artifacts: list[ArtifactRow],
    ) -> None:
        self._unexpected()

    def record_decision_resolution(self, decision_id: str, *, choice: str, resolved_by: str, at: datetime) -> bool:
        return self._unexpected()  # type: ignore[return-value]

    def record_requeue(self, chunk_id: str, *, at: datetime) -> None:
        self._unexpected()

    def record_queue_position(self, chunk_id: str, *, position: float, at: datetime) -> None:
        self._unexpected()

    def add_pm_pointers(self, chunk_id: str, pointers: list[PmPointer], *, at: datetime) -> None:
        self._unexpected()

    def record_grouped(self, chunk_id: str, *, grouped_into: str, at: datetime) -> None:
        self._unexpected()


def _route(chunk_id: str = "chk_1") -> Route:
    return Route(chunk_id=chunk_id, runner_id="rn_1", workspace_id="ws_1", environment_ids=["env_1"], created_at=_T0)


def test_detach_releases_the_live_route_with_the_injected_clocks_now() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo(route=_route())
    service = DetachService(chunks=repo, clock=clock)

    service.detach(_CHUNK)

    assert repo.released == [("chk_1", _T0)]


def test_detach_raises_not_routed_and_writes_nothing_when_there_is_no_live_route() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo(route=None)
    service = DetachService(chunks=repo, clock=clock)

    with pytest.raises(NotRouted):
        service.detach(_CHUNK)

    assert repo.released == []


def test_detach_uses_the_injected_clock_not_the_wall_clock() -> None:
    later = datetime(2026, 6, 1, tzinfo=UTC)
    clock = FixedClock(instant=later)
    repo = _FakeChunkRepo(route=_route())
    service = DetachService(chunks=repo, clock=clock)

    service.detach(_CHUNK)

    assert repo.released == [("chk_1", later)]
