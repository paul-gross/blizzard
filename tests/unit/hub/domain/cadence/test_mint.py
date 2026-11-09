"""The cadence mint's pure decisions: which holder a run reads, whether a re-read run moved, and the lock set's
canonical order — errand, then ranking."""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.cadence.errands import ErrandKey, ErrandRun, LastRun, holder_of
from blizzard.hub.domain.cadence.mint import mint_lock_keys, run_moved
from blizzard.hub.domain.cadence.ports import ERRAND_LOCK_NAMESPACE
from blizzard.hub.domain.chunk.model import ChunkFacts
from blizzard.hub.domain.chunk.ports.queue import BACKLOG_RANKING_LOCK, READY_RANKING_LOCK
from blizzard.hub.domain.urgency.model import UrgencyId
from blizzard.hub.domain.urgency.services import default_lock_keys

_AT = datetime(2026, 11, 5, 19, tzinfo=UTC)
_LIVE = ChunkFacts(minted=True)
_STOPPED = ChunkFacts(minted=True, stopped=True, stopped_at=_AT)
_KEY = ErrandKey("chr_1", "scp_1")
_URGENCY = UrgencyId("urg_1")


def _run(work_item_id: str, *, holder: ChunkFacts | None) -> ErrandRun:
    return ErrandRun(work_item_id, LastRun(minted_at=_AT, holder=holder_of({"ch": holder} if holder else {})))


def test_a_live_holder_outranks_a_newer_terminal_one() -> None:
    held = holder_of({"ch_1": _LIVE, "ch_2": _STOPPED})

    assert held is not None
    assert held.status is ChunkStatus.NOT_READY


def test_with_no_live_holder_the_newest_terminal_one_holds_and_none_holds_nothing() -> None:
    held = holder_of({"ch_1": ChunkFacts(minted=True, stopped=True, stopped_at=_AT.replace(hour=1)), "ch_2": _STOPPED})

    assert held is not None
    assert held.completed_at == _AT
    assert holder_of({}) is None


def test_a_run_moved_when_it_is_another_or_unfinished_or_newly_there() -> None:
    settled = _run("wi_1", holder=_STOPPED)

    assert not run_moved(None, None)
    assert not run_moved(settled, settled)
    assert run_moved(None, settled)
    assert run_moved(settled, _run("wi_2", holder=_STOPPED))
    assert run_moved(settled, _run("wi_1", holder=_LIVE))
    assert run_moved(settled, None)


def test_the_lock_set_sorts_the_errand_ahead_of_the_ranking_and_urgency_locks() -> None:
    promoting = mint_lock_keys(_KEY, promote=True, default_id=_URGENCY)
    defaults = default_lock_keys(_URGENCY)

    assert sorted(promoting)[0][0] == ERRAND_LOCK_NAMESPACE
    assert sorted(promoting)[1:3] == sorted([READY_RANKING_LOCK, BACKLOG_RANKING_LOCK])
    assert set(promoting[1:]) == {READY_RANKING_LOCK, BACKLOG_RANKING_LOCK, *defaults}
    assert mint_lock_keys(_KEY, promote=False, default_id=_URGENCY) == [promoting[0], *defaults]
    assert mint_lock_keys(ErrandKey("a,b", "c"), promote=False, default_id=_URGENCY) != mint_lock_keys(
        ErrandKey("a", "b,c"), promote=False, default_id=_URGENCY
    )
