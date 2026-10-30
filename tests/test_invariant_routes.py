"""The invariant checker served on request: ``GET /api/admin/invariants`` on the hub, run by hub administrators alone,
and ``GET /api/host/invariants`` on the runner host's human lane. Each answers its half's breaches as
``{invariant, detail}`` and refuses a second run while one holds the lock with ``409``."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from blizzard.auth_core import Role
from blizzard.foundation.invariants import Violation
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.config import AUTH_MODE_OAUTH
from blizzard.hub.store import schema as hub_schema
from blizzard.runner.config import RunnerConfig
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.status.invariants import HostInvariantCheck
from blizzard.runner.store import schema as runner_schema
from blizzard.tools.invariants import runner_invariants
from tests.config_inputs import create_runner_app
from tests.host_files import one_workspace
from tests.runner_fakes import FIXTURE_RUNNER_ID, FakeProbe, migrated_store_at
from tests.support import (
    HubHarness,
    build_hub,
    default_tenant,
    runner_token,
    seed_hub_admin,
    seed_runner,
    seed_session,
    seed_user,
    stamp,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
_HUB_ROUTE = "/api/admin/invariants"
_HOST_ROUTE = "/api/host/invariants"


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)


def _hub_admin(hub: HubHarness) -> dict[str, str]:
    user = seed_user(hub, username="hal", role=Role.PENDING)
    seed_hub_admin(hub.engine, user.user_id)
    return {"Authorization": f"Bearer {seed_session(hub, user)}"}


class _Held:
    """A checker whose run signals ``entered`` and waits on ``release``."""

    def __init__(self) -> None:
        self.entered, self.release = threading.Event(), threading.Event()

    def run(self, *, after_recovery: bool = False) -> list[Violation]:
        self.entered.set()
        self.release.wait(timeout=10)
        return []


def test_the_hub_route_refuses_a_request_with_no_session(hub: HubHarness) -> None:
    assert hub.client.get(_HUB_ROUTE).status_code == 401


def test_the_hub_route_refuses_a_tenant_admin_who_is_no_hub_administrator(hub: HubHarness) -> None:
    session = seed_session(hub, seed_user(hub, username="tia", role=Role.ADMIN))

    response = hub.client.get(
        _HUB_ROUTE, headers={"Authorization": f"Bearer {session}", TENANT_HEADER: default_tenant(hub.engine)}
    )

    assert response.status_code == 403


def test_the_hub_route_refuses_a_runner_principal(hub: HubHarness) -> None:
    seed_runner(hub, "r1")

    response = hub.client.get(
        _HUB_ROUTE, headers={"Authorization": f"Bearer {runner_token('r1')}", TENANT_HEADER: default_tenant(hub.engine)}
    )

    assert response.status_code == 403


def test_a_hub_administrator_reads_an_empty_list_from_a_clean_store(hub: HubHarness) -> None:
    response = hub.client.get(_HUB_ROUTE, headers=_hub_admin(hub))

    assert (response.status_code, response.json()) == (200, {"violations": []})


def test_a_seeded_hub_breach_comes_back_as_its_slug_and_detail(hub: HubHarness) -> None:
    with hub.engine.begin() as conn:
        for _ in range(2):
            conn.execute(
                sa.insert(hub_schema.delivery_repo_landed).values(
                    stamp(
                        hub_schema.delivery_repo_landed,
                        chunk_id="ch_1",
                        repo="toy-api",
                        commit_hash="abc",
                        landed_at=_NOW,
                    )
                )
            )

    response = hub.client.get(_HUB_ROUTE, headers=_hub_admin(hub))

    assert response.status_code == 200
    assert {"invariant": "hub:per-repo-land-idempotent", "detail": "chunk ch_1 repo toy-api landed 2 times"} in (
        response.json()["violations"]
    )
    assert all(set(v) == {"invariant", "detail"} for v in response.json()["violations"])


def test_a_hub_check_while_another_runs_answers_409(hub: HubHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    headers = _hub_admin(hub)
    check = hub.hub_services.invariants
    held = _Held()
    monkeypatch.setattr(check, "_checker", held)
    running = threading.Thread(target=lambda: hub.client.get(_HUB_ROUTE, headers=headers))
    running.start()
    assert held.entered.wait(timeout=10)

    try:
        refused = hub.client.get(_HUB_ROUTE, headers=headers)
    finally:
        held.release.set()
        running.join(timeout=10)

    assert (refused.status_code, refused.json()) == (409, {"code": "invariants_in_progress"})
    assert hub.client.get(_HUB_ROUTE, headers=headers).status_code == 200


def _runner_config(tmp_path: Path) -> RunnerConfig:
    config = RunnerConfig(
        runner_id=FIXTURE_RUNNER_ID,
        root=tmp_path,
        db_url=HostLayout(tmp_path).default_db_url,
        workspaces=one_workspace(root=str(tmp_path / "workspace")),
    )
    config.data_dir.mkdir(parents=True, exist_ok=True)
    migrated_store_at(config.db_url)
    return config


def test_the_host_route_answers_a_seeded_breach_as_its_slug_and_detail(tmp_path: Path) -> None:
    config = _runner_config(tmp_path)
    engine = create_engine_from_url(config.db_url)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(runner_schema.runner_identity).values(
                runner_id=FIXTURE_RUNNER_ID, runner_name="r", registered_at=_NOW
            )
        )
        conn.execute(
            sa.insert(runner_schema.outbound_buffer).values(
                runner_id=FIXTURE_RUNNER_ID, seq=1, kind="lease.minted", payload="{}", created_at=_NOW
            )
        )
    check = HostInvariantCheck(runner_invariants(engine, process=FakeProbe()))
    client = TestClient(create_runner_app(config, host_invariants=check))

    response = client.get(_HOST_ROUTE)

    assert response.status_code == 200
    assert response.json() == {
        "violations": [
            {
                "invariant": "runner:outbox-counter-above-seq",
                "detail": f"runner {FIXTURE_RUNNER_ID}: outbound lane retains seq 1 but has no counter",
            }
        ]
    }


def test_the_host_route_runs_the_live_process_check(tmp_path: Path) -> None:
    calls: list[bool] = []

    class _Recording:
        def run(self, *, after_recovery: bool = False) -> list[Violation]:
            calls.append(after_recovery)
            return []

    client = TestClient(create_runner_app(_runner_config(tmp_path), host_invariants=HostInvariantCheck(_Recording())))

    assert (client.get(_HOST_ROUTE).json(), calls) == ({"violations": []}, [True])


def test_a_host_check_while_another_runs_answers_409(tmp_path: Path) -> None:
    held = _Held()
    check = HostInvariantCheck(held)
    client = TestClient(create_runner_app(_runner_config(tmp_path), host_invariants=check))
    running = threading.Thread(target=lambda: client.get(_HOST_ROUTE))
    running.start()
    assert held.entered.wait(timeout=10)

    try:
        refused = client.get(_HOST_ROUTE)
    finally:
        held.release.set()
        running.join(timeout=10)

    assert (refused.status_code, refused.json()) == (409, {"code": "invariants_in_progress"})
    assert client.get(_HOST_ROUTE).status_code == 200
