"""The flow routes and the chunk reads' places (component tier), over a wired hub whose ``merge`` node lands with an
action holding each visit ``pending``: every place the routes and the chunk reads answer is the one the line judges,
``active`` keeps exactly the flows a board shows, and the record pages newest first to its end."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.hub.api.board_window import BOARD_DONE_WINDOW
from blizzard.hub.domain.actions.effects import ActionEscalation, Pass
from blizzard.hub.domain.delivery.flows import Submission
from blizzard.hub.store.internal.flow_store import FlowStore
from tests.support import hub_store_connections, seed_runner
from tests.test_flow_driver import World

pytestmark = pytest.mark.component


class Line:
    """Two chunks in one line at ``merge``, the first arrived first, each past its first ``pending`` turn."""

    def __init__(self, tmp_path: Path) -> None:
        self.world = World(tmp_path)
        self.client = self.world.hub.client
        self.first = self.world.at_merge(ref="1")
        self.world.hub.clock.advance(timedelta(seconds=1))
        self.second = self.world.at_merge(ref="2")
        self.world.hub.settle_turns()
        [self.flow_id] = [flow["flow_id"] for flow in self.client.get("/api/flows").json()["flows"]]

    def submission(self, chunk_id: str) -> Submission:
        [sub] = self.world.submissions(chunk_id)
        return sub

    def line(self) -> list[tuple[str, str, int]]:
        body = self.client.get(f"/api/flows/{self.flow_id}").json()
        return [(place["chunk_id"], place["state"], place["position"]) for place in body["line"]]

    def chunk_places(self, chunk_id: str) -> list[tuple[str, str, int]]:
        """The chunk's places as the chunk list and the chunk detail answer them — asserted equal."""
        rows = {row["chunk_id"]: row for row in self.client.get("/api/chunks").json()["chunks"]}
        listed = [(p["flow_id"], p["state"], p["position"]) for p in rows[chunk_id]["flow_places"]]
        detail = [(p["flow_id"], p["state"], p["position"]) for p in self.world.detail(chunk_id)["flow_places"]]
        assert listed == detail
        return listed

    def take_turn(self, chunk_id: str) -> None:
        store = FlowStore(hub_store_connections(self.world.hub.engine), self.world.hub.clock)
        turn = store.take_turn(
            self.submission(chunk_id).submission_id, passed_over=frozenset(), terms=(), at=self.world.hub.clock.now()
        )
        assert turn is not None


@pytest.fixture
def line(tmp_path: Path) -> Line:
    return Line(tmp_path)


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
