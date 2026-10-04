"""Re-enrolling a runner rotates its token: the replaced token is recorded revoked, so it is
refused under every runner-auth mode rather than merely failing to resolve."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from blizzard.foundation.tokens import TokenHash
from blizzard.hub.config import RUNNER_AUTH_ENFORCE, RUNNER_AUTH_WARN
from blizzard.hub.store.internal.runner_registry_store import locked_token_hash
from tests.support import build_hub

pytestmark = pytest.mark.component


def _enroll(hub) -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/runners/runner-a/enrollments")
    assert resp.status_code == 201, resp.text
    return str(resp.json()["token"])


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_a_first_enrollment_revokes_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/fleet/runners", json={"runner_id": "runner-a", "workspace_id": "ws-a"}).is_success

    token = _enroll(hub)

    assert not hub.services.registry.is_token_revoked(TokenHash(token).hex)


@pytest.mark.parametrize("mode", [RUNNER_AUTH_WARN, RUNNER_AUTH_ENFORCE])
def test_a_rotated_out_token_is_revoked_and_refused_under_every_mode(tmp_path: Path, mode: str) -> None:
    seed = build_hub(tmp_path)
    assert seed.client.post("/api/fleet/runners", json={"runner_id": "runner-a", "workspace_id": "ws-a"}).is_success
    old = _enroll(seed)
    new = _enroll(seed)
    hub = build_hub(tmp_path, runner_auth_mode=mode)

    assert hub.services.registry.is_token_revoked(TokenHash(old).hex)
    assert not hub.services.registry.is_token_revoked(TokenHash(new).hex)
    assert hub.client.get("/api/fleet/queue/peek", headers=_bearer(old)).status_code == 401
    assert hub.client.get("/api/fleet/queue/peek", headers=_bearer(new)).status_code == 200


@pytest.mark.unit
def test_rotation_and_revocation_read_the_hash_under_the_registration_row_lock() -> None:
    sql = str(locked_token_hash("runner-a").compile(dialect=postgresql.dialect()))
    assert sql.rstrip().endswith("FOR UPDATE")


def test_every_token_three_enrollments_mint_is_current_or_revoked(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/fleet/runners", json={"runner_id": "runner-a", "workspace_id": "ws-a"}).is_success

    *rotated_out, current = [_enroll(hub) for _ in range(3)]

    assert all(hub.services.registry.is_token_revoked(TokenHash(t).hex) for t in rotated_out)
    assert not hub.services.registry.is_token_revoked(TokenHash(current).hex)
