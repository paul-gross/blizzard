"""QueueService (unit tier) — the ready-queue sort key, facts only (issue #137).

:meth:`QueueService._effective_position` is a pure function over already-loaded dicts
(``bzh:domain-takes-objects``), unit-tested with zero store. :meth:`reposition` writes
through a fake chunk repository (``bzh:repository-split``) whose ``record_queue_position``
mutates ``positions`` in place for the retry path."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.queue import QueueService
from blizzard.hub.domain.work import Chunk, IWriteChunkRepository

pytestmark = pytest.mark.unit

_MINTED = datetime(2020, 1, 1, tzinfo=UTC)  # long ago
_PROMOTED = datetime(2026, 1, 1, tzinfo=UTC)  # promoted much later
_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _chunk(chunk_id: str, minted_at: datetime = _MINTED) -> Chunk:
    return Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=minted_at)


def test_never_promoted_chunk_falls_back_to_minted_at() -> None:
    chunk = _chunk("chk_1")
    position = QueueService._effective_position(chunk, {}, {})
    assert position == _MINTED.timestamp()


def test_promoted_but_unmoved_chunk_falls_back_to_promoted_at_not_minted_at() -> None:
    # A chunk minted long ago but promoted late (issue #137's fix): its fallback sort
    # key is the later promoted_at, so it lands at the tail, not mid-queue by mint order.
    chunk = _chunk("chk_1")
    position = QueueService._effective_position(chunk, {}, {"chk_1": _PROMOTED})
    assert position == _PROMOTED.timestamp()
    assert position > _MINTED.timestamp()


def test_explicit_position_wins_over_both_promoted_at_and_minted_at() -> None:
    chunk = _chunk("chk_1")
    position = QueueService._effective_position(chunk, {"chk_1": 3.0}, {"chk_1": _PROMOTED})
    assert position == 3.0


def test_ordering_by_effective_position_places_a_late_promoted_old_mint_chunk_last() -> None:
    # a: minted long ago, promoted late. b: minted after a, promoted right away. Without
    # the promoted_at fallback, a would wrongly sort before b on older minted_at.
    a = _chunk("chk_a", minted_at=_MINTED)
    b = _chunk("chk_b", minted_at=datetime(2025, 1, 1, tzinfo=UTC))
    promoted_ats = {"chk_a": _PROMOTED, "chk_b": datetime(2025, 1, 1, 0, 0, 1, tzinfo=UTC)}

    ordered = sorted([a, b], key=lambda c: QueueService._effective_position(c, {}, promoted_ats))

    assert [c.chunk_id for c in ordered] == ["chk_b", "chk_a"]


# --- QueueService.reposition — single-chunk fractional write (issue #137) --------


@dataclass
class _FakeChunkRepo:
    """Only the members :meth:`QueueService.reposition` (and the :meth:`replace_order`
    it may fall through to) touch are live; anything else is a bug."""

    ready: list[Chunk] = field(default_factory=list)
    positions: dict[str, float] = field(default_factory=dict)
    promoted_ats_by_chunk: dict[str, datetime] = field(default_factory=dict)
    stamped: list[tuple[str, float, datetime]] = field(default_factory=list)

    def list_ready(self) -> list[Chunk]:
        return self.ready

    def queue_positions(self) -> dict[str, float]:
        return dict(self.positions)

    def promoted_ats(self) -> dict[str, datetime]:
        return dict(self.promoted_ats_by_chunk)

    def record_queue_position(self, chunk_id: str, *, position: float, at: datetime) -> None:
        self.stamped.append((chunk_id, position, at))
        self.positions[chunk_id] = position

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"QueueService.reposition should not touch {name!r}")


def _as_write_repo(repo: _FakeChunkRepo) -> IWriteChunkRepository:
    """Assert the fake satisfies the Protocol QueueService depends on (see module docstring)."""
    return cast(IWriteChunkRepository, repo)


def test_reposition_between_two_neighbours_lands_on_their_midpoint() -> None:
    a, b, c, m = _chunk("chk_a"), _chunk("chk_b"), _chunk("chk_c"), _chunk("chk_m")
    repo = _FakeChunkRepo(ready=[a, b, c, m], positions={"chk_a": 0.0, "chk_b": 1.0, "chk_c": 2.0, "chk_m": 5.0})
    service = QueueService(chunks=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    service.reposition(m, after=a)

    assert repo.stamped[-1] == ("chk_m", 0.5, _T0)


def test_reposition_to_the_top_lands_below_the_current_lowest() -> None:
    a, b, m = _chunk("chk_a"), _chunk("chk_b"), _chunk("chk_m")
    repo = _FakeChunkRepo(ready=[a, b, m], positions={"chk_a": 1.0, "chk_b": 2.0, "chk_m": 5.0})
    service = QueueService(chunks=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    service.reposition(m, after=None)

    assert repo.stamped[-1] == ("chk_m", 0.0, _T0)


def test_reposition_to_the_top_of_an_otherwise_empty_ready_set_is_zero() -> None:
    m = _chunk("chk_m")
    repo = _FakeChunkRepo(ready=[m], positions={"chk_m": 5.0})
    service = QueueService(chunks=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    service.reposition(m, after=None)

    assert repo.stamped[-1] == ("chk_m", 0.0, _T0)


def test_reposition_after_the_last_chunk_lands_one_past_it() -> None:
    a, b, m = _chunk("chk_a"), _chunk("chk_b"), _chunk("chk_m")
    repo = _FakeChunkRepo(ready=[a, b, m], positions={"chk_a": 0.0, "chk_b": 1.0, "chk_m": 5.0})
    service = QueueService(chunks=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    service.reposition(m, after=b)

    assert repo.stamped[-1] == ("chk_m", 2.0, _T0)


def test_reposition_between_adjacent_doubles_renormalizes_then_succeeds() -> None:
    # a and b sit on adjacent representable doubles — no float is strictly between them,
    # so a plain midpoint would collide with one neighbour rather than landing between.
    a_pos = 1.0
    b_pos = math.nextafter(a_pos, math.inf)
    assert math.nextafter(a_pos, b_pos) >= b_pos  # the exhaustion condition this guards

    a, b, m = _chunk("chk_a"), _chunk("chk_b"), _chunk("chk_m")
    repo = _FakeChunkRepo(ready=[a, b], positions={"chk_a": a_pos, "chk_b": b_pos})
    service = QueueService(chunks=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    service.reposition(m, after=a)

    # Renormalize restamped a/b (and m, inserted between them) with dense ascending
    # floats; the final write lands strictly between the freshly-spread neighbours.
    assert repo.positions["chk_a"] < repo.positions["chk_m"] < repo.positions["chk_b"]
