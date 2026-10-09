"""``GET /api/admin/events`` over the hub app with sign-in on: a hub administrator reads the hub's own event log,
newest first and as many rows as asked; no tenant member — a tenant's admin included — reads it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.auth_core import Role
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.config import AUTH_MODE_OAUTH
from tests.support import HubHarness, build_hub, default_tenant, seed_hub_admin, seed_session, seed_user

pytestmark = pytest.mark.component

_T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    record = hub.hub_services.hub_event_log.record
    record(kind="trace-export-failed", message="export failed", detail={"error": "disk"}, at=_T0)
    record(kind="trace-export-recovered", message="export recovered", detail=None, at=_T0 + timedelta(minutes=1))
    record(kind="egress-cursor-reset", message="cursor moved", detail=None, at=_T0 + timedelta(minutes=2))
    return hub


def _hub_admin_session(hub: HubHarness) -> str:
    user = seed_user(hub, username="hal", role=Role.PENDING)
    seed_hub_admin(hub.engine, user.user_id)
    return seed_session(hub, user)


def test_a_hub_administrator_reads_the_hub_events_newest_first(hub: HubHarness) -> None:
    session = _hub_admin_session(hub)

    resp = hub.client.get("/api/admin/events", headers={"Authorization": f"Bearer {session}"})

    assert resp.status_code == 200
    rows = [(e["kind"], e["message"], e["detail"]) for e in resp.json()]
    assert rows[:3] == [
        ("egress-cursor-reset", "cursor moved", None),
        ("trace-export-recovered", "export recovered", None),
        ("trace-export-failed", "export failed", {"error": "disk"}),
    ]
    assert [e["severity"] for e in resp.json()[:3]] == ["info", "info", "warning"]


def test_the_limit_bounds_the_rows_read(hub: HubHarness) -> None:
    session = _hub_admin_session(hub)

    resp = hub.client.get("/api/admin/events?limit=1", headers={"Authorization": f"Bearer {session}"})
    refused = hub.client.get("/api/admin/events?limit=0", headers={"Authorization": f"Bearer {session}"})

    assert [e["kind"] for e in resp.json()] == ["egress-cursor-reset"]
    assert refused.status_code == 422


def test_a_tenant_admin_never_reads_the_hub_events(hub: HubHarness) -> None:
    tenant = default_tenant(hub.engine)
    session = seed_session(hub, seed_user(hub, username="tia", role=Role.ADMIN))

    resp = hub.client.get("/api/admin/events", headers={"Authorization": f"Bearer {session}", TENANT_HEADER: tenant})

    assert resp.status_code == 403
