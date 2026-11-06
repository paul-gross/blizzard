"""Chunk landing history: bounded pages and tenant/operator admission."""

from __future__ import annotations

from pathlib import Path

from blizzard.auth_core import Role
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision, WorkRef
from tests.support.hub_node_artifacts import record_landing
from tests.support.hub_request_auth import bearer, cookie, enrolled_runner_token, ingest_chunk
from tests.support.scaffolding import (
    add_tenant,
    build_hub,
    chunk_stores,
    count_queries,
    count_rows_read,
    default_tenant,
    seed_session,
    seed_user,
)


def test_page_cost_stays_flat_as_chunk_history_grows(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk = ingest_chunk(hub)
    artifacts = chunk_stores(hub.engine, hub.clock).artifacts
    decision = CloseEnqueueDecision(closable=frozenset({WorkRef(source="default", ref="1")}), skipped=frozenset())

    def add_facts(start: int, end: int) -> None:
        for i in range(start, end):
            record_landing(
                artifacts,
                chunk,
                repo=f"repo-{i}",
                commit_hash=f"sha-{i}",
                at=hub.clock.now(),
                close_decision=decision,
            )

    def page() -> None:
        response = hub.client.get(f"/api/chunks/{chunk}/landing-facts", params={"limit": 2})
        assert response.status_code == 200
        assert [item["repo"] for item in response.json()["items"]] == ["repo-0", "repo-1"]
        assert response.json()["next_cursor"] is not None

    add_facts(0, 4)
    page()
    small = count_queries(hub.engine, page), count_rows_read(hub.engine, page)
    add_facts(4, 40)
    large = count_queries(hub.engine, page), count_rows_read(hub.engine, page)
    assert large == small


def test_foreign_and_unknown_chunks_are_indistinguishable(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk = ingest_chunk(hub)
    other = add_tenant(hub.engine, "bravo")
    hub.hub_services.tenants.open_tenants.refresh()
    headers = {TENANT_HEADER: other}
    assert hub.client.get(f"/api/chunks/{chunk}/landing-facts", headers=headers).status_code == 404
    assert hub.client.get("/api/chunks/ch_never/landing-facts", headers=headers).status_code == 404
    assert (
        hub.client.get(
            f"/api/chunks/{chunk}/landing-facts", headers={TENANT_HEADER: default_tenant(hub.engine)}
        ).status_code
        == 200
    )


def test_landing_history_authentication_and_guest_permission(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    assert hub.client.get("/api/chunks/ch_x/landing-facts").status_code == 401
    contributor = seed_session(hub, seed_user(hub, username="ada", role=Role.CONTRIBUTOR))
    chunk = ingest_chunk(hub, headers=cookie(contributor))
    guest = seed_session(hub, seed_user(hub, username="grace", role=Role.GUEST))
    response = hub.client.get(f"/api/chunks/{chunk}/landing-facts", headers=cookie(guest))
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


def test_landing_history_refuses_runner_principal(tmp_path: Path) -> None:
    token = enrolled_runner_token(tmp_path)
    hub = build_hub(tmp_path, auth_mode="oauth")
    assert hub.client.get("/api/chunks/ch_x/landing-facts", headers=bearer(token)).status_code == 403
