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

from blizzard.foundation.chunk_status import TERMINAL_STATUSES, ChunkStatus
from blizzard.hub.api.chunks import BOARD_DONE_WINDOW
from tests.support import HubHarness, count_queries
from tests.support_chunk_world import (
    OLD,
    complete,
    done_by_transition,
    every_branch,
    hub_with_graph,
    mint,
    stop,
    transition,
)

pytestmark = pytest.mark.component


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
    hub = hub_with_graph(tmp_path)
    done_by_transition(hub, "ch_done_inside", ago=timedelta(hours=47, minutes=59))
    done_by_transition(hub, "ch_done_outside", ago=timedelta(hours=48, minutes=1))
    mint(hub, "ch_stopped_old", ago=OLD)
    stop(hub, "ch_stopped_old", ago=OLD)
    mint(hub, "ch_live_old", ago=OLD)
    transition(hub, "ch_live_old", "nd_1", epoch=1, ago=OLD)
    mint(hub, "ch_stopped_then_completed", ago=OLD)
    stop(hub, "ch_stopped_then_completed", ago=OLD)
    complete(hub, "ch_stopped_then_completed", ago=OLD - timedelta(hours=1))

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
    hub = hub_with_graph(tmp_path)
    every_branch(hub)
    all_facts = hub.services.chunks.facts.load_all_facts()

    assert all_facts["ch_mixed_tie_old"].status() is ChunkStatus.DONE  # the residue the handler drops
    expected_window = _oracle_window(hub)
    assert "ch_mixed_tie_old" not in expected_window
    assert "ch_stopped_old" in expected_window
    # A short page walk exercises the windowed keyset paging across the residue drop.
    for limit in (200, 3):
        listed = _listed(hub, board_window=True, limit=limit)
        assert len(listed) == len(set(listed))
        assert set(listed) == expected_window, limit

    oracle = Counter(facts.status() for facts in all_facts.values())
    assert hub.services.chunks.facts.status_counts() == {status: oracle[status] for status in ChunkStatus}


def test_chunk_counts_count_all_time_and_exclude_ephemeral_chunks(tmp_path: Path) -> None:
    hub = hub_with_graph(tmp_path)
    every_branch(hub)

    counts = hub.client.get("/api/chunk-counts")
    assert counts.status_code == 200
    body = counts.json()
    assert set(body) == {"total", "terminal"} | {status.value for status in ChunkStatus}

    unfiltered = _listed(hub, board_window=False)
    assert body["total"] == len(unfiltered) == sum(body[status.value] for status in ChunkStatus)
    assert "ch_deleted_done" not in unfiltered

    listed_rows = hub.client.get("/api/chunks").json()["chunks"]
    statuses = Counter(c["status"] for c in listed_rows)
    assert {status.value: body[status.value] for status in ChunkStatus} == {
        status.value: statuses[status.value] for status in ChunkStatus
    }
    # Terminal is the hub's judgment on both reads: the count sums exactly the rows flagged terminal.
    assert body["terminal"] == sum(body[status.value] for status in TERMINAL_STATUSES) > 0
    assert body["terminal"] == sum(1 for c in listed_rows if c["terminal"])
    assert all(c["terminal"] == (ChunkStatus(c["status"]) in TERMINAL_STATUSES) for c in listed_rows)
    # The counts ignore the window: every old done chunk still counts.
    assert body["done"] > len(
        [c for c in hub.client.get("/api/chunks?board_window=true").json()["chunks"] if c["status"] == "done"]
    )


def _hub_with_old_terminal(tmp_path: Path, *, old_terminal: int) -> HubHarness:
    hub = hub_with_graph(tmp_path)
    for i in range(4):
        mint(hub, f"ch_live_{i}", ago=timedelta(hours=i), promote=i % 2 == 0)
    done_by_transition(hub, "ch_recent_done", ago=timedelta(hours=1))
    for i in range(old_terminal):
        chunk_id = f"ch_old_{i:03d}"
        if i % 3 == 0:
            done_by_transition(hub, chunk_id, ago=OLD)
        else:
            mint(hub, chunk_id, ago=OLD)
            complete(hub, chunk_id, ago=OLD)
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
