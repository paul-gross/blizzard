"""PromoteService (unit tier) — promote-and-tail-stamp, facts only (issue #137).

A fake stands in for the store — only the methods :meth:`PromoteService.promote` calls
are meaningfully implemented; every other seam raises loudly if called
(``bzh:domain-core`` — no store, no tokens).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.queue import IWriteChunkQueueRepository
from blizzard.hub.domain.chunks.record import IReadChunkRecordRepository
from blizzard.hub.domain.promote import PromoteService
from blizzard.hub.domain.work import Chunk, ChunkFacts

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _chunk(chunk_id: str, minted_at: datetime = _T0) -> Chunk:
    return Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=minted_at)


@dataclass
class _FakeChunkRepo:
    """Only the members :meth:`PromoteService.promote` touches are live; anything else
    is a bug."""

    facts: ChunkFacts | None
    ready: list[Chunk] = field(default_factory=list)
    positions: dict[str, float] = field(default_factory=dict)
    promoted_ats_by_chunk: dict[str, datetime] = field(default_factory=dict)
    promoted: list[tuple[str, datetime]] = field(default_factory=list)
    stamped: list[tuple[str, float, datetime]] = field(default_factory=list)
    promoted_return: int | None = 1

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        return self.facts

    def list_ready(self) -> list[Chunk]:
        return self.ready

    def queue_positions(self) -> dict[str, float]:
        return self.positions

    def promoted_ats(self) -> dict[str, datetime]:
        return self.promoted_ats_by_chunk

    def record_promote_with_tail_position(self, chunk_id: str, *, position: float, at: datetime) -> int | None:
        self.promoted.append((chunk_id, at))
        self.stamped.append((chunk_id, position, at))
        return self.promoted_return

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"PromoteService should not touch {name!r}")


def _as_facts(repo: _FakeChunkRepo) -> IReadChunkFactsRepository:
    """Assert the fake satisfies the Protocol PromoteService depends on (see module docstring)."""
    return cast(IReadChunkFactsRepository, repo)


def _as_record(repo: _FakeChunkRepo) -> IReadChunkRecordRepository:
    return cast(IReadChunkRecordRepository, repo)


def _as_queue(repo: _FakeChunkRepo) -> IWriteChunkQueueRepository:
    return cast(IWriteChunkQueueRepository, repo)


def test_promote_stamps_zero_when_no_chunk_is_currently_ready() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo(facts=ChunkFacts(minted=True), ready=[])
    service = PromoteService(facts=_as_facts(repo), record=_as_record(repo), queue=_as_queue(repo), clock=clock)

    service.promote("chk_1")

    assert repo.promoted == [("chk_1", _T0)]
    assert repo.stamped == [("chk_1", 0.0, _T0)]


def test_promote_stamps_one_past_the_max_effective_position_of_ready_chunks() -> None:
    clock = FixedClock(instant=_T0)
    ready = [_chunk("chk_a"), _chunk("chk_b")]
    repo = _FakeChunkRepo(
        facts=ChunkFacts(minted=True),
        ready=ready,
        positions={"chk_a": 4.0, "chk_b": 1.0},
    )
    service = PromoteService(facts=_as_facts(repo), record=_as_record(repo), queue=_as_queue(repo), clock=clock)

    service.promote("chk_new")

    assert repo.stamped == [("chk_new", 5.0, _T0)]


def test_promote_uses_the_effective_position_fallback_for_ready_chunks_with_no_explicit_position() -> None:
    # A ready chunk with no explicit position falls back to its promoted_at (issue #137) —
    # the new chunk's tail stamp must beat that fallback too, not just explicit positions.
    clock = FixedClock(instant=_T0)
    ready = [_chunk("chk_a", minted_at=datetime(2020, 1, 1, tzinfo=UTC))]
    repo = _FakeChunkRepo(
        facts=ChunkFacts(minted=True),
        ready=ready,
        positions={},
        promoted_ats_by_chunk={"chk_a": datetime(2025, 6, 1, tzinfo=UTC)},
    )
    service = PromoteService(facts=_as_facts(repo), record=_as_record(repo), queue=_as_queue(repo), clock=clock)

    service.promote("chk_new")

    expected = datetime(2025, 6, 1, tzinfo=UTC).timestamp() + 1.0
    assert repo.stamped == [("chk_new", expected, _T0)]


def test_promote_is_a_complete_no_op_on_an_already_promoted_chunk() -> None:
    # No new chunk.promoted fact and no re-stamped queue position: a repeated promote
    # must not shove an already-ready chunk to the back of the queue.
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo(facts=ChunkFacts(minted=True, promoted=True))
    service = PromoteService(facts=_as_facts(repo), record=_as_record(repo), queue=_as_queue(repo), clock=clock)

    service.promote("chk_1")

    assert repo.promoted == []
    assert repo.stamped == []


def test_promote_uses_the_injected_clock_not_the_wall_clock() -> None:
    later = datetime(2026, 6, 1, tzinfo=UTC)
    clock = FixedClock(instant=later)
    repo = _FakeChunkRepo(facts=ChunkFacts(minted=True), ready=[])
    service = PromoteService(facts=_as_facts(repo), record=_as_record(repo), queue=_as_queue(repo), clock=clock)

    service.promote("chk_1")

    assert repo.promoted == [("chk_1", later)]
    assert repo.stamped == [("chk_1", 0.0, later)]
