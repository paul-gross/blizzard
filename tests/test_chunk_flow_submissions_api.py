"""``GET /api/chunks/{chunk_id}/flow-submissions`` (component tier): the chunk's submissions in arrival order with
their closures, the operator-plane auth triad, the runner principal refused, and an unknown or foreign chunk 404."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.auth_core import Role
from blizzard.foundation.clock import FixedClock
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.domain.delivery.flows import FlowResource
from blizzard.hub.store.internal.flow_store import FlowStore
from tests.support import add_tenant, build_hub, hub_store_connections, seed_session, seed_user
from tests.test_transcript_segments_api import _bearer, _cookie, _enrolled_runner_token, _ingest_chunk

pytestmark = pytest.mark.component

_AT = datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)
_MAIN = FlowResource("repo_a", "main")


def _flows(hub) -> FlowStore:
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
                "closure": {"outcome": "superseded", "closed_at": "2026-10-27T12:00:02+00:00"},
            },
            {
                "submission_id": second.submission_id,
                "node_id": "nd_deliver",
                "epoch": 1,
                "generation": 1,
                "arrival": 3,
                "submitted_at": "2026-10-27T12:00:01+00:00",
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
