"""The close diagnostic is tenant-scoped, bounded and contains no failure details."""

from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.auth_core import Role
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision, WorkItemCloseOutcome, WorkRef
from tests.support import (
    add_tenant,
    build_hub,
    chunk_stores,
    count_queries,
    count_rows_read,
    default_tenant,
    seed_session,
    seed_user,
)
from tests.test_transcript_segments_api import _bearer, _cookie, _enrolled_runner_token, _ingest_chunk

pytestmark = pytest.mark.component


def test_close_diagnostic_pages_and_hides_failure_details(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk = _ingest_chunk(hub)
    delivery = chunk_stores(hub.engine, hub.clock).delivery
    ref = WorkRef(source="default", ref="1")
    decision = CloseEnqueueDecision(closable=frozenset({ref}), skipped=frozenset())
    delivery.record_delivery_landed(chunk, at=hub.clock.now(), close_decision=decision)
    intent = next(i for i in delivery.pending_close_intents() if i.chunk_id == chunk)
    resume = hub.clock.now() + timedelta(minutes=5)

    def add(count: int) -> None:
        for _ in range(count):
            delivery.record_close_attempt_deferred(intent.intent_id, at=hub.clock.now(), resume_at=resume)

    def page() -> None:
        response = hub.client.get(f"/api/chunks/{chunk}/close-diagnostic", params={"limit": 2})
        assert response.status_code == 200
        body = response.json()
        assert len(body["attempts"]) == 2
        assert body["next_attempt_cursor"] == body["attempts"][-1]["sequence"]
        assert body["intents"][0]["retired_at"] is None
        assert body["closures"] == []

    add(4)
    page()
    small = count_queries(hub.engine, page), count_rows_read(hub.engine, page)
    add(36)
    assert (count_queries(hub.engine, page), count_rows_read(hub.engine, page)) == small
    first = hub.client.get(f"/api/chunks/{chunk}/close-diagnostic", params={"limit": 2}).json()
    second = hub.client.get(
        f"/api/chunks/{chunk}/close-diagnostic", params={"limit": 2, "attempt_cursor": first["next_attempt_cursor"]}
    ).json()
    assert first["attempts"][-1]["sequence"] < second["attempts"][0]["sequence"]
    assert all(a["outcome"] == "deferred" and a["resume_at"] for a in first["attempts"])
    delivery.record_work_item_closure(
        chunk, pointer=ref, outcome=WorkItemCloseOutcome.FAILED, reason="secret-outbound-payload", at=hub.clock.now()
    )
    assert "secret-outbound-payload" not in hub.client.get(f"/api/chunks/{chunk}/close-diagnostic").text


def test_close_diagnostic_isolation_and_auth(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    assert hub.client.get("/api/chunks/ch_x/close-diagnostic").status_code == 401
    contributor = seed_session(hub, seed_user(hub, username="ada", role=Role.CONTRIBUTOR))
    chunk = _ingest_chunk(hub, headers=_cookie(contributor))
    guest = seed_session(hub, seed_user(hub, username="grace", role=Role.GUEST))
    assert hub.client.get(f"/api/chunks/{chunk}/close-diagnostic", headers=_cookie(guest)).status_code == 200
    other = add_tenant(hub.engine, "bravo")
    hub.hub_services.tenants.open_tenants.refresh()
    headers = {**_cookie(guest), TENANT_HEADER: other}
    assert hub.client.get(f"/api/chunks/{chunk}/close-diagnostic", headers=headers).status_code in (403, 404)


def test_foreign_chunk_and_unknown_chunk_are_indistinguishable(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk = _ingest_chunk(hub)
    other = add_tenant(hub.engine, "bravo")
    hub.hub_services.tenants.open_tenants.refresh()
    headers = {TENANT_HEADER: other}
    assert hub.client.get(f"/api/chunks/{chunk}/close-diagnostic", headers=headers).status_code == 404
    assert hub.client.get("/api/chunks/ch_never/close-diagnostic", headers=headers).status_code == 404
    assert (
        hub.client.get(
            f"/api/chunks/{chunk}/close-diagnostic", headers={TENANT_HEADER: default_tenant(hub.engine)}
        ).status_code
        == 200
    )


def test_close_diagnostic_refuses_runner(tmp_path: Path) -> None:
    token = _enrolled_runner_token(tmp_path)
    hub = build_hub(tmp_path, auth_mode="oauth")
    assert hub.client.get("/api/chunks/ch_x/close-diagnostic", headers=_bearer(token)).status_code == 403
