"""Runner enrollment + the registration auth check (component tier, issue #86a).

Drives the real hub over a tmp store: ``POST /runners/{id}/enrollments`` mints/rotates
a bearer token (plaintext returned once, only its sha256 hash stored), and
``POST /runners`` applies ``require_runner_principal``/``assert_owns`` in ``warn`` (the
default — logs and proceeds) or ``enforce`` (rejects) mode, selected by
``runner_auth_mode`` at hub build time.

Registration itself is auth-checked, so seeding a runner to enroll (and to present its
token back to a **second**, ``enforce``-mode registration call) has to happen under
``warn`` first — an ``enforce`` hub would reject the very bootstrap registration a test
needs before it has a token to present. ``build_hub`` is called twice per such test,
both pointed at the same ``tmp_path`` (the same on-disk sqlite file, migrated
idempotently): once under ``warn`` to seed, once under ``enforce`` to drive the check.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from blizzard.hub.config import RUNNER_AUTH_ENFORCE
from blizzard.hub.store import schema as s
from tests.support import HubHarness, build_hub

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


def _seed_enrolled(tmp_path: Path, runner_id: str = "runner-a", workspace_id: str = "ws-a") -> str:
    """Register + enroll ``runner_id`` under a throwaway ``warn`` hub; return its token."""
    warn_hub = build_hub(tmp_path)
    _register(warn_hub, runner_id=runner_id, workspace_id=workspace_id)
    resp = _enroll(warn_hub, runner_id)
    return str(resp.json()["token"])


# --------------------------------------------------------------------------- #
# Enrollment itself
# --------------------------------------------------------------------------- #


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
    # by presenting each on a second registration call under `enforce`.
    enforce_hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    stale = _register(enforce_hub, runner_id="runner-a", workspace_id="ws-a", token=old_token)
    assert stale.status_code == 401

    fresh = _register(enforce_hub, runner_id="runner-a", workspace_id="ws-a", token=new_token)
    assert fresh.status_code == 201


# --------------------------------------------------------------------------- #
# `POST /runners` under `warn` (the default)
# --------------------------------------------------------------------------- #


def test_registration_under_warn_with_no_token_proceeds(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert _register(hub).status_code == 201


def test_registration_under_warn_with_an_invalid_token_proceeds(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = _register(hub, token="not-a-real-token")
    assert resp.status_code == 201


def test_registration_under_warn_with_a_mismatched_token_proceeds(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub, runner_id="runner-a", workspace_id="ws-a")
    token = _enroll(hub, "runner-a").json()["token"]

    # runner-a's token presented while declaring runner-b — a mismatch, still let through.
    resp = _register(hub, runner_id="runner-b", workspace_id="ws-b", token=token)
    assert resp.status_code == 201


# --------------------------------------------------------------------------- #
# `POST /runners` under `enforce`
# --------------------------------------------------------------------------- #


def test_registration_under_enforce_with_no_token_is_rejected(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    assert _register(hub).status_code == 401


def test_registration_under_enforce_with_an_invalid_token_is_rejected(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    resp = _register(hub, token="not-a-real-token")
    assert resp.status_code == 401


def test_registration_under_enforce_with_a_valid_token_succeeds(tmp_path: Path) -> None:
    token = _seed_enrolled(tmp_path, "runner-a", "ws-a")

    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    resp = _register(hub, runner_id="runner-a", workspace_id="ws-a", token=token)
    assert resp.status_code == 201


def test_registration_under_enforce_with_a_mismatched_token_is_rejected(tmp_path: Path) -> None:
    token = _seed_enrolled(tmp_path, "runner-a", "ws-a")

    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    resp = _register(hub, runner_id="runner-b", workspace_id="ws-b", token=token)
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# `registration_for_token_hash` resolves the right row among several
# --------------------------------------------------------------------------- #


def test_each_runners_token_resolves_only_its_own_registration(tmp_path: Path) -> None:
    token_a = _seed_enrolled(tmp_path, "runner-a", "ws-a")
    token_b = _seed_enrolled(tmp_path, "runner-b", "ws-b")

    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    assert _register(hub, runner_id="runner-a", workspace_id="ws-a", token=token_a).status_code == 201
    assert _register(hub, runner_id="runner-b", workspace_id="ws-b", token=token_b).status_code == 201
    # Cross-presented tokens are a mismatch, not a resolution failure — 403, not 401.
    assert _register(hub, runner_id="runner-b", workspace_id="ws-b", token=token_a).status_code == 403
