"""The flow routes and the chunk reads' places (component tier), over a wired hub whose ``merge`` node lands with an
action holding each visit ``pending``: every place the routes and the chunk reads answer is the one the line judges,
``active`` keeps exactly the flows a board shows, and the record pages newest first to its end."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.hub.api.board_window import BOARD_DONE_WINDOW
from blizzard.hub.domain.actions.effects import ActionEscalation, Pass
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision, WorkRef
from blizzard.hub.domain.delivery.flows import Submission, SubmissionRecord
from blizzard.hub.store.internal.flow_store import FlowStore
from tests.support.flow_driver_world import (
    _GRAPH,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.flow_driver_world
    World,
)
from tests.support.flow_route_lines import Line
from tests.support.scaffolding import chunk_stores, hub_store_connections, report_lease, seed_runner


@pytest.fixture
def line(tmp_path: Path) -> Line:
    return Line(tmp_path)


def test_chunk_landing_fact_pages(line: Line) -> None:
    delivery = chunk_stores(line.world.hub.engine, line.world.hub.clock).delivery
    for repo in ("z", "a"):
        delivery.record_delivery_repo_landed(
            line.first,
            repo=repo,
            commit_hash=repo,
            repository_id=None,
            at=line.world.hub.clock.now(),
            close_decision=CloseEnqueueDecision(
                closable=frozenset({WorkRef(source="default", ref="1")}), skipped=frozenset()
            ),
        )
    url = f"/api/chunks/{line.first}/landing-facts"
    first = line.client.get(url, params={"limit": 1}).json()
    second = line.client.get(url, params={"limit": 1, "cursor": first["next_cursor"]}).json()
    assert [first["items"][0]["repo"], second["items"][0]["repo"]] == ["z", "a"]
    assert second["next_cursor"] is None
    assert line.client.get("/api/chunks/ch_missing/landing-facts").status_code == 404
    assert line.client.get(url, params={"limit": 0}).status_code == 422


def test_each_place_on_the_flow_and_on_the_chunk_reads_is_the_lines(line: Line) -> None:
    view = line.client.get(f"/api/flows/{line.flow_id}").json()
    assert [(p["state"], p["next_poll_at"] is not None) for p in view["line"]] == [
        ("polling", True),
        ("polling", True),
    ]

    line.world.poll_due()

    assert line.line() == [(line.first, "next", 1), (line.second, "waiting", 2)]
    assert line.chunk_places(line.first) == [(line.flow_id, "next", 1)]
    assert line.chunk_places(line.second) == [(line.flow_id, "waiting", 2)]


def test_a_turn_open_on_the_flow_is_landing_and_the_one_behind_it_next(line: Line) -> None:
    line.world.poll_due()
    line.take_turn(line.first)

    view = line.client.get(f"/api/flows/{line.flow_id}").json()

    assert view["turn"]["chunk_id"] == line.first
    assert line.line() == [(line.first, "landing", 1), (line.second, "next", 1)]
    assert line.chunk_places(line.first) == [(line.flow_id, "landing", 1)]


def test_a_chunk_a_person_holds_is_passed_over_and_the_one_behind_it_next(line: Line) -> None:
    line.world.poll_due()
    line.world.action.answer = Pass(
        choice=None, escalation=ActionEscalation(EscalationCause.FORGE_AUTH, "token revoked")
    )
    line.world.tick()
    assert line.world.detail(line.first)["status"] == "needs_human"

    assert line.line() == [(line.first, "passed-over", 1), (line.second, "next", 1)]
    assert line.chunk_places(line.first) == [(line.flow_id, "passed-over", 1)]

    assert line.client.post(f"/api/chunks/{line.first}/pause", json={}).status_code == 202
    line.world.hub.clock.advance(timedelta(seconds=1))
    assert line.client.post(f"/api/chunks/{line.first}/requeues").status_code == 202
    assert line.world.detail(line.first)["status"] == "paused"

    assert line.chunk_places(line.first) == [(line.flow_id, "passed-over", 1)]


def test_a_chunks_detail_carries_every_fact_its_submissions_wrote(line: Line) -> None:
    record = line.world.detail(line.first)["flow_record"]

    assert [(f["flow_id"], f["kind"], f["outcome"]) for f in record] == [
        (line.flow_id, "submitted", None),
        (line.flow_id, "turn-started", None),
        (line.flow_id, "turn-ended", "pending"),
    ]
    assert [f["id"] for f in record] == sorted(f["id"] for f in record)


def test_active_keeps_exactly_the_flows_in_line_or_moved_within_the_board_window(line: Line) -> None:
    def active() -> list[str]:
        return [flow["flow_id"] for flow in line.client.get("/api/flows", params={"active": True}).json()["flows"]]

    assert active() == [line.flow_id]
    for chunk_id in (line.first, line.second):
        assert line.client.post(f"/api/chunks/{chunk_id}/stop", json={}).status_code == 202
    view = line.client.get(f"/api/flows/{line.flow_id}").json()
    assert view["line"] == []
    assert active() == [line.flow_id]

    line.world.hub.clock.advance(BOARD_DONE_WINDOW - timedelta(seconds=1))
    assert active() == [line.flow_id]
    line.world.hub.clock.advance(timedelta(seconds=2))

    assert active() == []
    assert [flow["flow_id"] for flow in line.client.get("/api/flows").json()["flows"]] == [line.flow_id]


def test_a_submission_closed_between_the_chunk_reads_holds_no_place(
    line: Line, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chunk reads find the chunks' open submissions, then read the line over their flows. A submission that
    closes in between — its chunk bounced off the node — holds no place in either read, never a failed one."""
    closing = line.submission(line.first)
    store = FlowStore(hub_store_connections(line.world.hub.engine), line.world.hub.clock)
    found_open = FlowStore.open_submissions_of

    def then_closed(self: FlowStore, chunk_ids: Sequence[str]) -> list[Submission]:
        found = found_open(self, chunk_ids)
        store.close(closing.submission_id, outcome="superseded", at=line.world.hub.clock.now())
        return found

    monkeypatch.setattr(FlowStore, "open_submissions_of", then_closed)

    assert line.chunk_places(line.first) == []
    assert line.chunk_places(line.second) == [(line.flow_id, "polling", 1)]


def test_a_withdrawal_is_no_verdict(line: Line) -> None:
    assert line.client.post(f"/api/chunks/{line.first}/stop", json={}).status_code == 202

    view = line.client.get(f"/api/flows/{line.flow_id}").json()

    assert view["last_verdict"] is None
    assert view["last_landing"] is None


def test_the_record_pages_newest_first_and_next_before_ends_the_walk(line: Line) -> None:
    ids: list[int] = []
    before: int | None = None
    while True:
        params = {"limit": 2, **({"before": before} if before is not None else {})}
        page = line.client.get(f"/api/flows/{line.flow_id}/record", params=params).json()
        ids.extend(fact["id"] for fact in page["facts"])
        before = page["next_before"]
        if before is None:
            break

    assert len(ids) == 6
    assert ids == sorted(ids, reverse=True)
    assert {fact["id"] for fact in line.world.detail(line.first)["flow_record"]} <= set(ids)


def test_a_runner_token_answers_403_on_every_flow_route(line: Line) -> None:
    runner = {"Authorization": f"Bearer {seed_runner(line.world.hub, 'r9')}"}

    for path in ("/api/flows", f"/api/flows/{line.flow_id}", f"/api/flows/{line.flow_id}/record"):
        assert line.client.get(path, headers=runner).status_code == 403, path


def test_another_tenants_flow_answers_404(line: Line) -> None:
    assert line.client.get("/api/flows/flw_missing").status_code == 404
    other = line.world.hub.add_tenant("other")

    assert other.client.get("/api/flows").json()["flows"] == []
    assert other.client.get(f"/api/flows/{line.flow_id}").status_code == 404
    assert other.client.get(f"/api/flows/{line.flow_id}/record").status_code == 404


def test_the_last_verdict_is_the_newest_turn_whose_outcome_closed_its_submission(line: Line) -> None:
    line.world.poll_due()
    line.world.action.answer = Pass(choice="failure")
    line.world.tick()

    verdict = line.client.get(f"/api/flows/{line.flow_id}").json()["last_verdict"]

    assert (verdict["chunk_id"], verdict["kind"], verdict["outcome"]) == (line.first, "turn-ended", "failure")
    assert line.line() == [(line.second, "next", 1)]


def test_each_turn_ended_entry_carries_its_verdict_and_a_wait_none(line: Line) -> None:
    line.world.poll_due()
    line.world.action.answer = Pass(choice="failure")
    line.world.tick()

    ended = [f for f in line.world.detail(line.first)["flow_record"] if f["kind"] == "turn-ended"]
    record = line.client.get(f"/api/flows/{line.flow_id}/record").json()["facts"]

    assert [(f["outcome"], f["verdict"], f["wait"], f["rework"]) for f in ended] == [
        ("pending", None, True, False),
        ("failure", "bounced", False, False),
    ]
    assert {(f["verdict"], f["wait"], f["rework"]) for f in record if f["kind"] != "turn-ended"} == {
        (None, False, False)
    }


def test_the_chunk_list_carries_the_latest_bounce_and_the_graph_progress(line: Line) -> None:
    line.world.poll_due()
    line.world.action.answer = Pass(choice="failure")
    line.world.tick()

    row = {r["chunk_id"]: r for r in line.client.get("/api/chunks").json()["chunks"]}[line.first]
    [bounce] = line.world.detail(line.first)["bounces"]

    assert (row["latest_bounce"], row["bounce_count"]) == (bounce, 1)
    assert (bounce["withdrawn"], bounce["rework"]) == (True, False)
    assert row["graph_name"] == line.world.detail(line.first)["graph_name"]
    assert [(n["name"], n["state"]) for n in row["graph_progress"]] == [("build", "current"), ("merge", "failed")]


def test_the_chunk_list_reads_every_bounced_chunks_submissions_in_one_read(
    line: Line, monkeypatch: pytest.MonkeyPatch
) -> None:
    line.world.action.answer = Pass(choice="failure")
    line.world.poll_due()
    line.world.tick()
    line.world.poll_due()
    line.world.tick()
    batched: list[list[str]] = []
    single: list[str] = []
    batch_read = FlowStore.submissions_of_chunks

    def spy_batch(self: FlowStore, chunk_ids: Sequence[str]) -> dict[str, list[SubmissionRecord]]:
        batched.append(sorted(chunk_ids))
        return batch_read(self, chunk_ids)

    def spy_single(self: FlowStore, chunk_id: str) -> list[SubmissionRecord]:
        single.append(chunk_id)
        return []

    monkeypatch.setattr(FlowStore, "submissions_of_chunks", spy_batch)
    monkeypatch.setattr(FlowStore, "submissions_of", spy_single)

    rows = {r["chunk_id"]: r for r in line.client.get("/api/chunks").json()["chunks"]}

    assert batched == [sorted([line.first, line.second])]
    assert single == []
    assert rows[line.first]["latest_bounce"]["withdrawn"] is True
    assert rows[line.second]["latest_bounce"]["withdrawn"] is True


#: The delivery graph with a ``conflict`` choice sending ``merge`` back to ``build``, and a cap of one bounce.
_CONFLICT_GRAPH = _GRAPH.replace(
    "    action: test.held-landing\n", "    action: test.held-landing\n    bounce_cap: 1\n", 1
).replace(
    """        failure:
          description: Failed to land.
          to: build
""",
    """        failure:
          description: Failed to land.
          to: build
        conflict:
          description: Conflicts with its base.
          to: build
""",
    1,
)


def test_a_conflict_bounce_is_rework_only_while_its_turn_routes_it_back_and_not_once_past_its_cap(
    tmp_path: Path,
) -> None:
    world = World(tmp_path, extra_answers=frozenset({"conflict"}))
    world.action.answer = Pass(choice="conflict")
    chunk_id = world.at_merge(_CONFLICT_GRAPH)
    world.hub.settle_turns()

    def latest() -> dict[str, Any]:
        row = {r["chunk_id"]: r for r in world.hub.client.get("/api/chunks").json()["chunks"]}[chunk_id]
        return row["latest_bounce"]

    first = latest()
    assert (first["cause"], first["withdrawn"], first["rework"]) == ("conflict", True, True)
    [ended] = [f for f in world.detail(chunk_id)["flow_record"] if f["kind"] == "turn-ended"]
    assert (ended["outcome"], ended["verdict"], ended["rework"]) == ("conflict", "bounced", True)

    detail = world.detail(chunk_id)
    assert detail["current_node_name"] == "build"
    # The bounce's transition back to ``build`` opened epoch 3 — the merge visit's 2 plus one.
    report_lease(world.hub, chunk_id, epoch=3, seq=2)
    world.complete(chunk_id, detail["current_node_id"], epoch=3)
    world.hub.settle_turns()

    second = latest()
    assert world.detail(chunk_id)["status"] == "needs_human"
    assert (second["cause"], second["withdrawn"], second["rework"]) == ("conflict", False, False)
