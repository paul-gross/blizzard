"""The board's windowed chunk read and the all-time status counts (component tier).

``GET /api/chunks?board_window=true`` drops exactly the ``done`` chunks that finished before
the window, and ``GET /api/chunk-counts`` counts every non-ephemeral chunk — both equal to
the full Python derivation over every terminal branch, with a cost that stays flat as only
the old terminal-chunk count grows. The window is widened by a floor: the newest ``BOARD_DONE_FLOOR``
``done`` chunks stay however old."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

from blizzard.foundation.chunk_status import TERMINAL_STATUSES, ChunkStatus
from blizzard.hub.api import chunks as chunks_api
from blizzard.hub.api.chunks import BOARD_DONE_FLOOR, BOARD_DONE_WINDOW
from blizzard.hub.domain.operations.promote import is_promotable
from blizzard.hub.store import schema as s
from tests.support import HubHarness, count_queries, default_tenant, seed_project
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


def _fill_floor(hub: HubHarness) -> set[str]:
    """Ten in-window done chunks, so the floor holds them and no older done chunk."""
    filler = {f"ch_filler_{i}" for i in range(BOARD_DONE_FLOOR)}
    for i, chunk_id in enumerate(sorted(filler)):
        done_by_transition(hub, chunk_id, ago=timedelta(hours=1, minutes=i))
    return filler


def test_the_window_keeps_recent_done_old_stopped_and_old_live_and_drops_old_done(tmp_path: Path) -> None:
    hub = hub_with_graph(tmp_path)
    filler = _fill_floor(hub)
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
    assert windowed == {"ch_done_inside", "ch_stopped_old", "ch_live_old"} | filler

    everything = filler | {
        "ch_done_inside",
        "ch_done_outside",
        "ch_stopped_old",
        "ch_live_old",
        "ch_stopped_then_completed",
    }
    assert set(_listed(hub, board_window=False)) == everything
    assert {c["chunk_id"] for c in hub.client.get("/api/chunks").json()["chunks"]} == everything
    assert hub.client.get("/api/chunks/ch_done_outside").status_code == 200


def _oracle_window(hub: HubHarness, floor: int) -> set[str]:
    """Every chunk but a ``done`` one outside both the window and the ``floor`` newest finished."""
    cutoff: datetime = hub.clock.now() - BOARD_DONE_WINDOW
    visible = set(_listed(hub, board_window=False))
    all_facts = {c: f for c, f in hub.services.chunks.facts.load_all_facts().items() if c in visible}
    # ``ch_mixed_tie_old`` is the tie residue the store's SQL cannot rank (``chunk_is_terminal``'s residue).
    ranked = sorted(
        (
            (facts.completed_at(), chunk_id)
            for chunk_id, facts in all_facts.items()
            if facts.status() is ChunkStatus.DONE
            and facts.completed_at() is not None
            and chunk_id != "ch_mixed_tie_old"
        ),
        reverse=True,
    )
    floor_ids = {chunk_id for _, chunk_id in ranked[:floor]}
    kept: set[str] = set()
    for chunk_id, facts in all_facts.items():
        completed_at = facts.completed_at()
        if (
            facts.status() is ChunkStatus.DONE
            and completed_at is not None
            and completed_at < cutoff
            and chunk_id not in floor_ids
        ):
            continue
        kept.add(chunk_id)
    return kept


@pytest.mark.parametrize("floor", [1, 3, BOARD_DONE_FLOOR])
def test_window_membership_and_counts_equal_the_full_derivation_over_every_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, floor: int
) -> None:
    monkeypatch.setattr(chunks_api, "BOARD_DONE_FLOOR", floor)
    hub = hub_with_graph(tmp_path)
    every_branch(hub)
    all_facts = hub.services.chunks.facts.load_all_facts()

    assert all_facts["ch_mixed_tie_old"].status() is ChunkStatus.DONE  # the residue the handler drops
    expected_window = _oracle_window(hub, floor)
    if floor == 1:
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
    assert set(body) == {"total", "terminal", "done_since"} | {status.value for status in ChunkStatus}

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
    # The counts ignore the window and the floor: every old done chunk still counts.
    assert body["done"] > len(
        [c for c in hub.client.get("/api/chunks?board_window=true").json()["chunks"] if c["status"] == "done"]
    )


def test_list_rows_carry_promotable_from_the_shared_promote_predicate(tmp_path: Path) -> None:
    hub = hub_with_graph(tmp_path)
    every_branch(hub)
    mint(hub, "ch_never_promoted", ago=timedelta(hours=1), promote=False)
    mint(hub, "ch_paused_never_promoted", ago=timedelta(hours=1), promote=False)
    assert hub.client.post("/api/chunks/ch_paused_never_promoted/pause", json={"by": "op"}).status_code == 202

    rows = {c["chunk_id"]: c for c in hub.client.get("/api/chunks").json()["chunks"]}
    all_facts = hub.services.chunks.facts.load_all_facts()

    assert (rows["ch_paused_never_promoted"]["status"], rows["ch_paused_never_promoted"]["promotable"]) == (
        "paused",
        True,
    )
    assert rows["ch_never_promoted"]["promotable"] is True
    assert not any(rows[chunk_id]["promotable"] for chunk_id in rows if all_facts[chunk_id].promoted)
    assert all(rows[chunk_id]["promotable"] is is_promotable(all_facts[chunk_id]) for chunk_id in rows)


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
    bare = _hub_with_old_terminal(tmp_path / "none", old_terminal=BOARD_DONE_FLOOR)
    heavy = _hub_with_old_terminal(tmp_path / "many", old_terminal=15 + BOARD_DONE_FLOOR)

    for path in ("/api/chunks?board_window=true&limit=2", "/api/chunk-counts"):
        assert count_queries(bare.engine, lambda p=path: bare.client.get(p)) == count_queries(
            heavy.engine, lambda p=path: heavy.client.get(p)
        ), path

    # Past the floor, more old done chunks change nothing the board lists: the floor holds the same
    # number of them either way, beside the same live and recent chunks.
    def board(hub: HubHarness) -> tuple[list[str], int]:
        ids = [c["chunk_id"] for c in hub.client.get("/api/chunks?board_window=true").json()["chunks"]]
        return [i for i in ids if not i.startswith("ch_old_")], sum(i.startswith("ch_old_") for i in ids)

    assert board(bare) == board(heavy)
    assert board(heavy)[1] == BOARD_DONE_FLOOR - 1  # the recent done chunk takes a floor slot


def _done_old(hub: HubHarness, count: int, *, prefix: str, ago: timedelta = OLD) -> list[str]:
    """``count`` done chunks finished ``ago`` back, one minute apart, oldest first."""
    ids = [f"ch_{prefix}_{i:03d}" for i in range(count)]
    for i, chunk_id in enumerate(ids):
        done_by_transition(hub, chunk_id, ago=ago + timedelta(minutes=count - i))
    return ids


def test_twenty_done_inside_the_window_all_stay(tmp_path: Path) -> None:
    hub = hub_with_graph(tmp_path)
    ids = _done_old(hub, 20, prefix="in", ago=timedelta(hours=2))
    assert set(_listed(hub, board_window=True)) == set(ids)


def test_a_few_inside_the_window_and_many_older_leave_the_ten_newest(tmp_path: Path) -> None:
    hub = hub_with_graph(tmp_path)
    older = _done_old(hub, 50, prefix="old")
    inside = _done_old(hub, 3, prefix="in", ago=timedelta(hours=2))
    expected = set(inside) | set(older[-(BOARD_DONE_FLOOR - len(inside)) :])
    assert len(expected) == BOARD_DONE_FLOOR
    for limit in (200, 3):
        assert set(_listed(hub, board_window=True, limit=limit)) == expected, limit


def test_none_inside_the_window_leaves_the_ten_newest(tmp_path: Path) -> None:
    hub = hub_with_graph(tmp_path)
    older = _done_old(hub, 25, prefix="old")
    assert set(_listed(hub, board_window=True)) == set(older[-BOARD_DONE_FLOOR:])


def test_the_floor_is_ranked_by_completion_not_by_minting(tmp_path: Path) -> None:
    hub = hub_with_graph(tmp_path)
    mint(hub, "ch_minted_last_finished_first", ago=OLD)
    complete(hub, "ch_minted_last_finished_first", ago=OLD + timedelta(days=3))
    older = _done_old(hub, BOARD_DONE_FLOOR, prefix="old")
    assert set(_listed(hub, board_window=True)) == set(older)


def test_the_project_lens_floors_that_projects_done_chunks_only(tmp_path: Path) -> None:
    hub = hub_with_graph(tmp_path)
    ids = _done_old(hub, 30, prefix="old")
    beta = seed_project(hub.engine, "beta")
    moved = ids[::2]  # alternate chunks belong to beta, the rest stay in the default project
    with hub.engine.begin() as conn:
        conn.execute(
            update(s.chunks)
            .where(s.chunks.c.tenant_id == default_tenant(hub.engine), s.chunks.c.chunk_id.in_(moved))
            .values(project_id=beta)
        )

    def listed(project: str) -> set[str]:
        params: dict[str, str | int | bool] = {"board_window": True, "project": project, "limit": 200}
        return {c["chunk_id"] for c in hub.client.get("/api/chunks", params=params).json()["chunks"]}

    assert listed("beta") == set(moved[-BOARD_DONE_FLOOR:])
    assert listed("default") == set(ids[1::2][-BOARD_DONE_FLOOR:])


def _done_since(hub: HubHarness, since: datetime, **params: str) -> int:
    resp = hub.client.get("/api/chunk-counts", params={"done_since": since.isoformat()} | params)
    assert resp.status_code == 200, resp.text
    return resp.json()["done_since"]


def test_done_since_counts_every_done_branch_finishing_at_or_after_the_instant(tmp_path: Path) -> None:
    hub = hub_with_graph(tmp_path)
    every_branch(hub)
    now = hub.clock.now()

    assert hub.client.get("/api/chunk-counts").json()["done_since"] is None
    everything = _done_since(hub, now - OLD - timedelta(days=1))
    assert everything == hub.client.get("/api/chunk-counts").json()["done"] > 0
    # The newer half finished an hour back: the same branches, counted once each.
    recent = _done_since(hub, now - timedelta(hours=2))
    assert 0 < recent < everything
    assert recent * 2 == everything
    assert _done_since(hub, now + timedelta(minutes=1)) == 0


def test_done_since_narrows_to_one_project(tmp_path: Path) -> None:
    hub = hub_with_graph(tmp_path)
    done_by_transition(hub, "ch_default", ago=timedelta(hours=1))
    done_by_transition(hub, "ch_beta", ago=timedelta(hours=1))
    beta = seed_project(hub.engine, "beta")
    with hub.engine.begin() as conn:
        conn.execute(
            update(s.chunks)
            .where(s.chunks.c.tenant_id == default_tenant(hub.engine), s.chunks.c.chunk_id == "ch_beta")
            .values(project_id=beta)
        )
    since = hub.clock.now() - timedelta(hours=2)

    assert _done_since(hub, since) == 2
    assert _done_since(hub, since, project=beta) == 1


def test_done_since_costs_the_same_statements_across_the_done_count(tmp_path: Path) -> None:
    (tmp_path / "none").mkdir()
    (tmp_path / "many").mkdir()
    bare = _hub_with_old_terminal(tmp_path / "none", old_terminal=0)
    heavy = _hub_with_old_terminal(tmp_path / "many", old_terminal=20)
    since = (bare.clock.now() - timedelta(days=1)).isoformat()
    path = f"/api/chunk-counts?done_since={since.replace('+', '%2B')}"

    assert count_queries(bare.engine, lambda: bare.client.get(path)) == count_queries(
        heavy.engine, lambda: heavy.client.get(path)
    )
