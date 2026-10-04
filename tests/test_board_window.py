"""The board's windowed chunk read and the all-time status counts (component tier).

``GET /api/chunks?board_window=true`` drops exactly the ``done`` chunks that finished before
the window, and ``GET /api/chunk-counts`` counts every non-ephemeral chunk — both equal to
the full Python derivation over every terminal branch, with a cost that stays flat as only
the old terminal-chunk count grows."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.api.chunks import BOARD_DONE_WINDOW
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.store import schema as s
from tests.support import HubHarness, build_hub, chunk_stores, count_queries, seed_graph

pytestmark = pytest.mark.component

_OLD = timedelta(days=30)


def _hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path)
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=hub.clock.now() - timedelta(days=60))
    return hub


def _mint(hub: HubHarness, chunk_id: str, *, ago: timedelta, promote: bool = True) -> None:
    at = hub.clock.now() - ago
    stores = chunk_stores(hub.engine, hub.clock)
    stores.record.mint(Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=at, default_model=[]))
    if promote:
        stores.queue.record_promote(chunk_id, at=at)


def _transition(hub: HubHarness, chunk_id: str, to_node: str, *, epoch: int, ago: timedelta, n: int = 0) -> None:
    with hub.engine.begin() as conn:
        conn.execute(
            insert(s.transitions).values(
                transition_id=f"tr_{chunk_id}_{epoch}_{to_node}_{n}",
                chunk_id=chunk_id,
                graph_id="gr_1",
                from_node_id=None,
                to_node_id=to_node,
                epoch=epoch,
                runner_id="r",
                recorded_at=hub.clock.now() - ago,
            )
        )


def _stop(hub: HubHarness, chunk_id: str, *, ago: timedelta) -> None:
    with hub.engine.begin() as conn:
        conn.execute(insert(s.chunk_stopped).values(chunk_id=chunk_id, stopped_at=hub.clock.now() - ago))


def _complete(hub: HubHarness, chunk_id: str, *, ago: timedelta) -> None:
    with hub.engine.begin() as conn:
        conn.execute(
            insert(s.chunk_completed).values(chunk_id=chunk_id, completed_at=hub.clock.now() - ago, completed_by="op")
        )


def _done_by_transition(hub: HubHarness, chunk_id: str, *, ago: timedelta) -> None:
    _mint(hub, chunk_id, ago=ago + timedelta(hours=1))
    _transition(hub, chunk_id, "nd_1", epoch=1, ago=ago + timedelta(minutes=30))
    _transition(hub, chunk_id, RESERVED_TERMINAL, epoch=2, ago=ago)


def _listed(hub: HubHarness, *, board_window: bool, limit: int = 200) -> list[str]:
    ids: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int | bool] = {"limit": limit, "board_window": board_window}
        if cursor is not None:
            params["cursor"] = cursor
        body = hub.client.get("/api/chunks", params=params).json()
        ids += [c["chunk_id"] for c in body["chunks"]]
        cursor = body["next_cursor"]
        if cursor is None:
            return ids


def test_the_window_keeps_recent_done_old_stopped_and_old_live_and_drops_old_done(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    _done_by_transition(hub, "ch_done_inside", ago=timedelta(hours=47, minutes=59))
    _done_by_transition(hub, "ch_done_outside", ago=timedelta(hours=48, minutes=1))
    _mint(hub, "ch_stopped_old", ago=_OLD)
    _stop(hub, "ch_stopped_old", ago=_OLD)
    _mint(hub, "ch_live_old", ago=_OLD)
    _transition(hub, "ch_live_old", "nd_1", epoch=1, ago=_OLD)
    _mint(hub, "ch_stopped_then_completed", ago=_OLD)
    _stop(hub, "ch_stopped_then_completed", ago=_OLD)
    _complete(hub, "ch_stopped_then_completed", ago=_OLD - timedelta(hours=1))

    windowed = set(_listed(hub, board_window=True))
    assert windowed == {"ch_done_inside", "ch_stopped_old", "ch_live_old"}

    everything = {
        "ch_done_inside",
        "ch_done_outside",
        "ch_stopped_old",
        "ch_live_old",
        "ch_stopped_then_completed",
    }
    assert set(_listed(hub, board_window=False)) == everything
    assert {c["chunk_id"] for c in hub.client.get("/api/chunks").json()["chunks"]} == everything
    assert hub.client.get("/api/chunks/ch_done_outside").status_code == 200


def _every_branch(hub: HubHarness) -> None:
    """Each terminal branch, once finished long ago and once inside the window, plus live
    chunks and an ephemeral one."""
    for label, ago in (("old", _OLD), ("new", timedelta(hours=1))):
        _mint(hub, f"ch_stopped_{label}", ago=ago)
        _stop(hub, f"ch_stopped_{label}", ago=ago)

        _mint(hub, f"ch_stop_then_complete_{label}", ago=ago)
        _stop(hub, f"ch_stop_then_complete_{label}", ago=ago)
        _complete(hub, f"ch_stop_then_complete_{label}", ago=ago)  # a tie goes to the completion

        _mint(hub, f"ch_complete_then_stop_{label}", ago=ago)
        _complete(hub, f"ch_complete_then_stop_{label}", ago=ago + timedelta(minutes=1))
        _stop(hub, f"ch_complete_then_stop_{label}", ago=ago)

        _mint(hub, f"ch_operator_completed_{label}", ago=ago)
        _complete(hub, f"ch_operator_completed_{label}", ago=ago)

        _done_by_transition(hub, f"ch_done_transition_{label}", ago=ago)

        _mint(hub, f"ch_restarted_{label}", ago=ago + timedelta(hours=1))
        _transition(hub, f"ch_restarted_{label}", RESERVED_TERMINAL, epoch=1, ago=ago + timedelta(minutes=1))
        with hub.engine.begin() as conn:
            conn.execute(
                insert(s.chunk_restarts).values(
                    chunk_id=f"ch_restarted_{label}",
                    graph_id="gr_1",
                    to_node_id="nd_1",
                    epoch=2,
                    restarted_by="op",
                    recorded_at=hub.clock.now() - ago,
                )
            )

        _mint(hub, f"ch_migrated_{label}", ago=ago + timedelta(hours=1))
        _transition(hub, f"ch_migrated_{label}", RESERVED_TERMINAL, epoch=1, ago=ago)
        with hub.engine.begin() as conn:
            conn.execute(
                insert(s.chunk_migrations).values(
                    migration_id=f"mg_{label}",
                    chunk_id=f"ch_migrated_{label}",
                    from_graph_id="gr_1",
                    to_graph_id="gr_1",
                    landed_node_id="nd_1",
                    epoch=1,
                    recorded_at=hub.clock.now() - ago,  # a tie with the terminal transition
                )
            )

        # Two same-instant terminal transitions supersede each other in the prefilter, which
        # keeps the chunk in; the derivation settles it done.
        _mint(hub, f"ch_tie_{label}", ago=ago + timedelta(hours=1))
        _transition(hub, f"ch_tie_{label}", RESERVED_TERMINAL, epoch=1, ago=ago, n=1)
        _transition(hub, f"ch_tie_{label}", RESERVED_TERMINAL, epoch=1, ago=ago, n=2)

    _mint(hub, "ch_ready", ago=_OLD)
    _mint(hub, "ch_not_ready", ago=_OLD, promote=False)
    _mint(hub, "ch_running", ago=_OLD)
    _transition(hub, "ch_running", "nd_1", epoch=1, ago=_OLD)
    _done_by_transition(hub, "ch_deleted_done", ago=_OLD)
    _mint(hub, "ch_deleted_live", ago=_OLD)
    with hub.engine.begin() as conn:
        for chunk_id in ("ch_deleted_done", "ch_deleted_live"):
            conn.execute(insert(s.chunk_deleted).values(chunk_id=chunk_id, deleted_at=hub.clock.now(), deleted_by="op"))


def _oracle_window(hub: HubHarness) -> set[str]:
    cutoff: datetime = hub.clock.now() - BOARD_DONE_WINDOW
    kept: set[str] = set()
    for chunk_id, facts in hub.services.chunks.facts.load_all_facts().items():
        completed_at = facts.completed_at()
        if facts.status() is ChunkStatus.DONE and completed_at is not None and completed_at < cutoff:
            continue
        kept.add(chunk_id)
    return kept


def test_window_membership_and_counts_equal_the_full_derivation_over_every_branch(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    _every_branch(hub)
    all_facts = hub.services.chunks.facts.load_all_facts()

    assert all_facts["ch_tie_old"].status() is ChunkStatus.DONE  # the residue the handler drops
    expected_window = _oracle_window(hub)
    assert "ch_tie_old" not in expected_window
    assert "ch_stopped_old" in expected_window
    # A short page walk exercises the windowed keyset paging across the residue drop.
    for limit in (200, 3):
        listed = _listed(hub, board_window=True, limit=limit)
        assert len(listed) == len(set(listed))
        assert set(listed) == expected_window, limit

    oracle = Counter(facts.status() for facts in all_facts.values())
    assert hub.services.chunks.facts.status_counts() == {status: oracle[status] for status in ChunkStatus}


def test_chunk_counts_count_all_time_and_exclude_ephemeral_chunks(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    _every_branch(hub)

    counts = hub.client.get("/api/chunk-counts")
    assert counts.status_code == 200
    body = counts.json()
    assert set(body) == {"total"} | {status.value for status in ChunkStatus}

    unfiltered = _listed(hub, board_window=False)
    assert body["total"] == len(unfiltered) == sum(body[status.value] for status in ChunkStatus)
    assert "ch_deleted_done" not in unfiltered

    statuses = Counter(c["status"] for c in hub.client.get("/api/chunks").json()["chunks"])
    assert {status.value: body[status.value] for status in ChunkStatus} == {
        status.value: statuses[status.value] for status in ChunkStatus
    }
    # The counts ignore the window: every old done chunk still counts.
    assert body["done"] > len(
        [c for c in hub.client.get("/api/chunks?board_window=true").json()["chunks"] if c["status"] == "done"]
    )


def _hub_with_old_terminal(tmp_path: Path, *, old_terminal: int) -> HubHarness:
    hub = _hub(tmp_path)
    for i in range(4):
        _mint(hub, f"ch_live_{i}", ago=timedelta(hours=i), promote=i % 2 == 0)
    _done_by_transition(hub, "ch_recent_done", ago=timedelta(hours=1))
    for i in range(old_terminal):
        chunk_id = f"ch_old_{i:03d}"
        if i % 3 == 0:
            _done_by_transition(hub, chunk_id, ago=_OLD)
        else:
            _mint(hub, chunk_id, ago=_OLD)
            _complete(hub, chunk_id, ago=_OLD)
    return hub


def test_both_reads_cost_the_same_statements_across_the_old_terminal_count(tmp_path: Path) -> None:
    (tmp_path / "none").mkdir()
    (tmp_path / "many").mkdir()
    bare = _hub_with_old_terminal(tmp_path / "none", old_terminal=0)
    heavy = _hub_with_old_terminal(tmp_path / "many", old_terminal=15)

    for path in ("/api/chunks?board_window=true&limit=2", "/api/chunk-counts"):
        assert count_queries(bare.engine, lambda p=path: bare.client.get(p)) == count_queries(
            heavy.engine, lambda p=path: heavy.client.get(p)
        ), path

    bare_page = bare.client.get("/api/chunks?board_window=true").json()["chunks"]
    heavy_page = heavy.client.get("/api/chunks?board_window=true").json()["chunks"]
    assert [c["chunk_id"] for c in bare_page] == [c["chunk_id"] for c in heavy_page]
