"""Runner enrollment + the registration auth check (component tier).

Drives the real hub over a tmp store: enrollment rotates an added runner's bearer token
(plaintext returned once, only its sha256 hash stored); registration admits only a token the
hub issued, under every configuration, and records the runner that token names whatever the
request body declares."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from blizzard.hub.store import schema as s
from tests.support import HubHarness, build_hub, seed_runner

pytestmark = pytest.mark.component


def _register(
    hub: HubHarness, runner_id: str = "runner-a", workspace_id: str = "ws-a", *, token: str | None = None
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else None
    return hub.client.post(
        "/api/fleet/runners", json={"runner_id": runner_id, "workspace_id": workspace_id}, headers=headers
    )


def _enroll(hub: HubHarness, runner_id: str = "runner-a") -> httpx.Response:
    return hub.client.post(f"/api/runners/{runner_id}/enrollments")


def _token_hash_column(hub: HubHarness, runner_id: str) -> str | None:
    with hub.engine.connect() as conn:
        row = conn.execute(
            select(s.runner_registrations.c.token_hash).where(s.runner_registrations.c.runner_id == runner_id)
        ).one()
        return row.token_hash


def _registrations(hub: HubHarness) -> int:
    with hub.engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(s.runner_registrations)).scalar_one())


# Enrollment itself


def test_enroll_unknown_runner_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert _enroll(hub, "ghost").status_code == 404


def test_enroll_prints_the_token_once_and_stores_only_its_hash(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)

    resp = _enroll(hub)
    assert resp.status_code == 201
    body = resp.json()
    assert body["runner_id"] == "runner-a"
    token = body["token"]
    assert token  # a plaintext, nonempty token

    stored = _token_hash_column(hub, "runner-a")
    assert stored == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert stored != token  # never the plaintext


def test_re_enroll_rotates_the_old_token_dead(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub, runner_id="runner-a", workspace_id="ws-a")
    old_token = _enroll(hub).json()["token"]

    new_token = _enroll(hub).json()["token"]
    assert new_token != old_token

    # The old token no longer resolves; the new one does — both asserted the same way,
    # by presenting each on a registration call.
    stale = _register(hub, runner_id="runner-a", workspace_id="ws-a", token=old_token)
    assert stale.status_code == 401

    fresh = _register(hub, runner_id="runner-a", workspace_id="ws-a", token=new_token)
    assert fresh.status_code == 201


# `POST /runners` — the token decides, under every configuration


@pytest.mark.parametrize("auth_mode", ["none", "oauth"])
def test_registration_with_no_token_is_refused_under_every_configuration(tmp_path: Path, auth_mode: str) -> None:
    hub = build_hub(tmp_path, auth_mode=auth_mode)
    assert hub.app is not None
    before = _registrations(hub)

    resp = TestClient(hub.app).post("/api/fleet/runners", json={"runner_id": "runner-a", "workspace_id": "ws-a"})

    assert resp.status_code == 401
    assert _registrations(hub) == before


def test_registration_with_a_token_the_hub_never_issued_is_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    before = _registrations(hub)
    assert _register(hub, token="not-a-real-token").status_code == 401
    assert _registrations(hub) == before


def test_registration_with_an_issued_token_succeeds(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = seed_runner(hub, "runner-a", register=False)

    resp = _register(hub, runner_id="runner-a", workspace_id="ws-a", token=token)

    assert resp.status_code == 201
    assert resp.json()["runner_id"] == "runner-a"


def test_registration_records_the_tokens_runner_whatever_the_body_declares(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = seed_runner(hub, "runner-a", register=False)

    resp = _register(hub, runner_id="runner-b", workspace_id="ws-b", token=token)

    assert resp.status_code == 201
    assert resp.json()["runner_id"] == "runner-a"
    assert hub.services.registry.get_runner("runner-b") is None
    registered = hub.services.registry.get_runner("runner-a")
    assert registered is not None and registered.workspace_id == "ws-b"


# `registration_for_token_hash` resolves the right row among several


def test_each_runners_token_resolves_only_its_own_registration(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token_a = seed_runner(hub, "runner-a", register=False)
    token_b = seed_runner(hub, "runner-b", register=False)

    assert _register(hub, runner_id="runner-a", token=token_a).json()["runner_id"] == "runner-a"
    assert _register(hub, runner_id="runner-a", token=token_b).json()["runner_id"] == "runner-b"
