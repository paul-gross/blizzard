"""``GET /api/chunks/{chunk_id}/flow-submissions`` (component tier): the chunk's submissions in arrival order with
their closures, the operator-plane auth triad, the runner principal refused, and an unknown or foreign chunk 404."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.auth_core import Role
from blizzard.foundation.clock import FixedClock
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.domain.delivery.flows import FlowResource, LandingTerms, TurnEvent
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.flow_store import FlowStore
from tests.support import (
    add_tenant,
    build_hub,
    capture_statements,
    count_queries,
    count_rows_read,
    explain_query_plan,
    hub_store_connections,
    seed_session,
    seed_user,
)
from tests.test_transcript_segments_api import _bearer, _cookie, _enrolled_runner_token, _ingest_chunk

pytestmark = pytest.mark.component

_AT = datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)
_MAIN = FlowResource("repo_a", "main")


def _flows(hub) -> FlowStore:  # type: ignore[no-untyped-def]
    return FlowStore(hub_store_connections(hub.engine), FixedClock(_AT))


def test_the_read_serves_every_submission_in_arrival_order_with_its_closure(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, other_chunk = _ingest_chunk(hub), _ingest_chunk(hub, pointer_token="default:2")
    flows = _flows(hub)
    first = flows.submit(chunk_id, node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN], at=_AT)
    flows.submit(other_chunk, node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN], at=_AT)
    second = flows.submit(
        chunk_id, node_id="nd_deliver", epoch=1, generation=1, resources=[_MAIN], at=_AT + timedelta(seconds=1)
    )
    assert first is not None and second is not None
    assert flows.close(first.submission_id, outcome="superseded", at=_AT + timedelta(seconds=2))

    resp = hub.client.get(f"/api/chunks/{chunk_id}/flow-submissions")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "chunk_id": chunk_id,
        "items": [
            {
                "submission_id": first.submission_id,
                "node_id": "nd_deliver",
                "epoch": 1,
                "generation": 0,
                "arrival": 1,
                "submitted_at": "2026-10-27T12:00:00+00:00",
                "flow_ids": [first.flows[0].flow_id],
                "terms": [],
                "closure": {"outcome": "superseded", "closed_at": "2026-10-27T12:00:02+00:00"},
            },
            {
                "submission_id": second.submission_id,
                "node_id": "nd_deliver",
                "epoch": 1,
                "generation": 1,
                "arrival": 3,
                "submitted_at": "2026-10-27T12:00:01+00:00",
                "flow_ids": [second.flows[0].flow_id],
                "terms": [],
                "closure": None,
            },
        ],
    }


def test_a_chunk_never_submitted_serves_no_items(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _ingest_chunk(hub)

    resp = hub.client.get(f"/api/chunks/{chunk_id}/flow-submissions")

    assert resp.status_code == 200
    assert resp.json() == {"chunk_id": chunk_id, "items": []}


def test_an_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/chunks/ch_never/flow-submissions")

    assert resp.status_code == 404


def test_another_tenants_chunk_is_404_as_if_never_minted(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _ingest_chunk(hub)
    _flows(hub).submit(chunk_id, node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN], at=_AT)
    other = add_tenant(hub.engine, "bravo")
    hub.hub_services.tenants.open_tenants.refresh()

    resp = hub.client.get(f"/api/chunks/{chunk_id}/flow-submissions", headers={TENANT_HEADER: other})

    assert resp.status_code == 404


def test_the_read_is_401_with_no_session(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")

    assert hub.client.get("/api/chunks/ch_x/flow-submissions").status_code == 401


def test_the_read_is_200_at_guest(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    contributor = seed_session(hub, seed_user(hub, username="ada", role=Role.CONTRIBUTOR))
    chunk_id = _ingest_chunk(hub, headers=_cookie(contributor))
    guest = seed_session(hub, seed_user(hub, username="grace", role=Role.GUEST))

    resp = hub.client.get(f"/api/chunks/{chunk_id}/flow-submissions", headers=_cookie(guest))

    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_the_read_refuses_a_runner_principal(tmp_path: Path) -> None:
    token = _enrolled_runner_token(tmp_path)
    hub = build_hub(tmp_path, auth_mode="oauth")

    assert hub.client.get("/api/chunks/ch_x/flow-submissions", headers=_bearer(token)).status_code == 403


def test_turn_read_requires_guest_session_and_refuses_runner(tmp_path: Path) -> None:
    token = _enrolled_runner_token(tmp_path)
    hub = build_hub(tmp_path, auth_mode="oauth")
    url = "/api/chunks/ch_x/flow-submissions/fs_x/turns"
    assert hub.client.get(url).status_code == 401
    assert hub.client.get(url, headers=_bearer(token)).status_code == 403
    guest = seed_session(hub, seed_user(hub, username="grace", role=Role.GUEST))
    assert hub.client.get(url, headers=_cookie(guest)).status_code == 404


def test_turn_page_cost_and_index_are_bounded_as_history_grows(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk = _ingest_chunk(hub)
    flows = _flows(hub)
    submission = flows.submit(chunk, node_id="nd_garden", epoch=1, generation=0, resources=[], at=_AT)
    assert submission is not None
    scoped = hub_store_connections(hub.engine)

    def add_turns(start: int, end: int) -> None:
        with scoped.write("seed_turns") as conn:
            for i in range(start, end):
                conn.execute(
                    scoped.insert(s.hub_turns).values(
                        turn_id=f"htn_{1000 - i:04d}",
                        submission_id=submission.submission_id,
                        started_at=_AT,
                        ended_at=_AT,
                        outcome="interrupted",
                    )
                )

    url = f"/api/chunks/{chunk}/flow-submissions/{submission.submission_id}/turns"

    def page() -> None:
        response = hub.client.get(url, params={"limit": 2, "before": "htn_1000"})
        assert response.status_code == 200
        assert [item["turn_id"] for item in response.json()["items"]] == ["htn_0999", "htn_0998"]
        assert response.json()["next_before"] == "htn_0998"

    add_turns(0, 4)
    small = count_queries(hub.engine, page), count_rows_read(hub.engine, page)
    add_turns(4, 80)
    large = count_queries(hub.engine, page), count_rows_read(hub.engine, page)
    assert large == small
    with capture_statements(hub.engine) as statements:
        page()
    turn_select = next((sql, params) for sql, params in statements if "FROM hub_turns" in sql)
    plan = [row.detail for row in explain_query_plan(hub.engine, *turn_select)]
    assert any("ix_hub_turns_tenant_submission_turn" in detail for detail in plan)
    assert not any("TEMP B-TREE" in detail for detail in plan)
    with capture_statements(hub.engine) as statements:
        assert hub.client.get(url, params={"limit": 2}).status_code == 200
    turn_select = next((sql, params) for sql, params in statements if "FROM hub_turns" in sql)
    plan = [row.detail for row in explain_query_plan(hub.engine, *turn_select)]
    assert any("ix_hub_turns_tenant_submission_turn" in detail for detail in plan)
    assert not any("TEMP B-TREE" in detail for detail in plan)


def test_captured_terms_and_paged_turns_include_zero_flow_submissions(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk = _ingest_chunk(hub)
    flows = _flows(hub)
    submission = flows.submit(chunk, node_id="nd_deliver", epoch=1, generation=0, resources=[_MAIN], at=_AT)
    garden = flows.submit(chunk, node_id="nd_garden", epoch=1, generation=0, resources=[], at=_AT)
    assert submission is not None and garden is not None
    terms = [LandingTerms("repo_a", "park", 2, 1.5)]
    first = flows.take_turn(submission.submission_id, passed_over=frozenset(), terms=terms, at=_AT)
    assert first is not None
    event = TurnEvent(kind="turn-interrupted", chunk_id=chunk, node_name="deliver", message="interrupted", detail={})
    assert (
        flows.end_turn_interrupted(first.turn_id, withdrawal=None, event=event, at=_AT + timedelta(seconds=1))
        is not None
    )
    second = flows.take_turn(submission.submission_id, passed_over=frozenset(), terms=[], at=_AT + timedelta(seconds=2))
    garden_turn = flows.take_turn(garden.submission_id, passed_over=frozenset(), terms=[], at=_AT)
    assert second is not None and garden_turn is not None

    items = hub.client.get(f"/api/chunks/{chunk}/flow-submissions").json()["items"]
    assert items[0]["flow_ids"] == [submission.flows[0].flow_id]
    assert items[0]["terms"] == [
        {"repository_id": "repo_a", "landing_policy": "park", "park_timeout_hours": 2, "landing_position": 1.5}
    ]
    assert items[1]["flow_ids"] == items[1]["terms"] == []
    url = f"/api/chunks/{chunk}/flow-submissions/{submission.submission_id}/turns"
    page = hub.client.get(url, params={"limit": 1}).json()
    assert page["items"][0]["turn_id"] == max(first.turn_id, second.turn_id)
    assert page["next_before"] == max(first.turn_id, second.turn_id)
    previous = hub.client.get(url, params={"limit": 1, "before": page["next_before"]}).json()
    assert previous["items"][0]["turn_id"] == min(first.turn_id, second.turn_id)
    turns = {item["turn_id"]: item for item in page["items"] + previous["items"]}
    assert turns[first.turn_id]["outcome"] == "interrupted"
    assert turns[second.turn_id]["ended_at"] is None
    assert previous["next_before"] is None
    garden_page = hub.client.get(f"/api/chunks/{chunk}/flow-submissions/{garden.submission_id}/turns").json()
    assert garden_page["items"][0]["turn_id"] == garden_turn.turn_id
    assert hub.client.get(url, params={"limit": 201}).status_code == 422
    assert hub.client.get(f"/api/chunks/ch_wrong/flow-submissions/{submission.submission_id}/turns").status_code == 404
    other = add_tenant(hub.engine, "bravo")
    hub.hub_services.tenants.open_tenants.refresh()
    assert hub.client.get(url, headers={TENANT_HEADER: other}).status_code == 404
