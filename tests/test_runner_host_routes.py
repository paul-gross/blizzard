"""The host's one origin: its own routes at the root, every runner's under ``/runners/{runner_id}``.

The route table is exhaustive — every operation is a root allowlisted route or begins with the runner prefix — and a
request names its runner by id: two runners route to their own seams, an unknown id is ``404``, a halted one ``503``,
and a runner route at its old root path is ``404`` rather than the panel. The ``/api/host/*`` reads compose each
runner's own stores, stamping its id. Exercised over a real store via TestClient."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.host_registration_state import HostRegistrationState
from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.foundation.runner_paths import is_api_path
from blizzard.foundation.web import Frontend
from blizzard.runner.api.wiring import ServedWorkspace
from blizzard.runner.app import create_app, create_app_for_export, runner_seams
from blizzard.runner.config import ProjectBinding, RunnerConfig
from blizzard.runner.harness.registry import HarnessRegistry
from blizzard.runner.host_config import HaltedRunner
from blizzard.runner.hub.client import BindingDeclaration
from blizzard.runner.hub.host_identity import HostIdentity, HostIdentityHolder
from blizzard.runner.hub.host_registration import HostRegistrationHolder, HostRegistrationStatus
from blizzard.runner.hub.identity import HostStanding, IdentityVerdictHolder
from blizzard.runner.status.view import RunnerStatusService
from blizzard.runner.stores import RunnerStores
from tests.host_files import one_workspace
from tests.runner_client import live_api_routes
from tests.runner_fakes import (
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_host_stores,
    make_store,
    make_stores,
    registered_identity,
    single_workspace,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)

#: The routes that belong to the host and answer at the root; every other operation is one runner's.
_ROOT_ALLOWLIST = ("/api/health", "/api/flags", "/api/ready", "/api/auth/", "/api/events/stream", "/api/host/")


def _status(store: SqlAlchemyRunnerStore, config: RunnerConfig) -> RunnerStatusService:
    return RunnerStatusService(
        FixedClock(_NOW),
        pause=store,
        daemon_liveness=store,
        lease_record=store,
        outbound=store,
        environments=store,
        asks=store,
        takeover=store,
        escalations=store,
        identity=registered_identity(config.served_id(), config.name),
        identity_verdict=None,
        runner_name=config.name,
        workspace_id=config.home_workspace.id,
        bindings=tuple(BindingDeclaration(b.project, b.workspace, b.agents) for b in config.bindings or ()),
        hub_url=config.hub_url,
        workspaces=single_workspace(FakeProvider({}), workspace_id="ws-1"),
        gates=(),
        runner_dir="",
        harnesses=HarnessRegistry({}),
    )


def _runner(
    tmp_path: Path, runner_id: str, name: str, *, bindings: tuple[ProjectBinding, ...]
) -> tuple[RunnerConfig, RunnerStores]:
    db_url = f"sqlite:///{tmp_path / 'host.db'}"
    config = RunnerConfig(
        root=tmp_path,
        db_url=db_url,
        runner_id=runner_id,
        name=name,
        workspaces=one_workspace(id="ws-1"),
        bindings=bindings,
    )
    return config, make_stores(make_store(db_url, runner_id))


def _two_runner_app(
    tmp_path: Path,
    *,
    halted: tuple[HaltedRunner, ...] = (),
    verdicts: tuple[IdentityVerdictHolder | None, IdentityVerdictHolder | None] = (None, None),
    registration: HostRegistrationHolder | None = None,
    host_identity: HostIdentityHolder | None = None,
) -> tuple[FastAPI, RunnerStores, RunnerStores]:
    a_config, a_stores = _runner(tmp_path, "rn_a", "r-claude", bindings=(ProjectBinding("blizzard", "ws-1", 2),))
    b_config, b_stores = _runner(tmp_path, "rn_b", "r-chatgpt", bindings=(ProjectBinding("mock", "ws-1", 1),))
    clock = FixedClock(_NOW)
    seams = tuple(
        runner_seams(
            config,
            clock=clock,
            process=None,  # type: ignore[arg-type]
            runner_stores=stores,
            runner_status=_status(stores.lease_record, config),  # type: ignore[arg-type]
            identity_verdict=verdict,
        )
        for config, stores, verdict in ((a_config, a_stores, verdicts[0]), (b_config, b_stores, verdicts[1]))
    )
    host_store = make_store(f"sqlite:///{tmp_path / 'host.db'}", None)
    app = create_app(
        a_config,
        runners=seams,
        halted=halted,
        workspaces=(ServedWorkspace("ws-1", ("e1", "e2", "e3"), 3),),
        host_stores=make_host_stores(host_store),
        host_registration=registration,
        host_identity=host_identity,
        clock=clock,
    )
    return app, a_stores, b_stores


def test_every_operation_is_a_root_host_route_or_under_the_runner_prefix() -> None:
    app = create_app_for_export()
    stray = [
        path
        for path, _route in live_api_routes(app)
        if not path.startswith("/runners/{runner_id}/") and not path.startswith(_ROOT_ALLOWLIST)
    ]
    assert stray == []
    document = app.openapi()
    for path, operations in document["paths"].items():
        if not path.startswith("/runners/{runner_id}/"):
            continue
        for operation in operations.values():
            names = [(p["name"], p["in"]) for p in operation.get("parameters", [])]
            assert ("runner_id", "path") in names, path


@pytest.mark.parametrize(
    "path", ["/api/dashboard", "/api/leases", "/api/runner", "/api/environments", "/api/subscriptions", "/v1/traces"]
)
def test_a_runner_route_at_its_old_root_path_is_404_not_the_panel(path: str) -> None:
    client = TestClient(create_app(RunnerConfig(root=Path("."), db_url="sqlite://")))

    response = client.post(path) if path.startswith("/v1") else client.get(path)

    assert response.status_code in (404, 405)
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize(
    ("path", "panel"),
    [
        ("/api/nope", False),
        ("/runners/rn_x/api/nope", False),
        ("/runners/rn_x/v1/traces", False),
        ("/runners/rn_x/board", True),
        ("/board", True),
    ],
)
def test_the_spa_fallback_answers_the_shell_only_off_the_api(tmp_path: Path, path: str, panel: bool) -> None:
    (tmp_path / "index.html").write_text("<html>panel</html>")
    app = FastAPI()
    Frontend(tmp_path, "blizzard-runner", is_api_path=is_api_path).mount(app)

    response = TestClient(app).get(path)

    assert (response.status_code, response.text == "<html>panel</html>") == ((200, True) if panel else (404, False))


def test_two_registered_runners_each_route_to_their_own_seams(tmp_path: Path) -> None:
    app, a_stores, _b_stores = _two_runner_app(tmp_path)
    a_stores.environments.record_binding(chunk_id="ch_a", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    client = TestClient(app)

    a = client.get("/runners/rn_a/api/runner").json()
    b = client.get("/runners/rn_b/api/runner").json()

    assert (a["runner_id"], a["runner_name"]) == ("rn_a", "r-claude")
    assert (b["runner_id"], b["runner_name"]) == ("rn_b", "r-chatgpt")
    client.patch("/runners/rn_b/api/runner", json={"paused": True})
    assert client.get("/runners/rn_b/api/runner").json()["pause"]["local"] is True
    assert client.get("/runners/rn_a/api/runner").json()["pause"]["local"] is False


def test_an_unknown_runner_id_is_404_and_a_halted_one_503_naming_its_reasons(tmp_path: Path) -> None:
    halted = HaltedRunner("r-broken", "rn_h", ("has no hub token",))
    app, _a, _b = _two_runner_app(tmp_path, halted=(halted,))
    client = TestClient(app)

    unknown = client.get("/runners/rn_nope/api/dashboard")
    stopped = client.get("/runners/rn_h/api/dashboard")

    assert unknown.status_code == 404
    assert stopped.status_code == 503
    assert "has no hub token" in stopped.json()["detail"]


def test_host_runners_lists_each_runner_served_or_halted(tmp_path: Path) -> None:
    halted = HaltedRunner("r-broken", None, ("has no id",))
    app, _a, _b = _two_runner_app(tmp_path, halted=(halted,))
    client = TestClient(app)
    client.patch("/runners/rn_b/api/runner", json={"paused": True})

    items = client.get("/api/host/runners").json()["items"]

    assert [(i["runner_id"], i["name"], i["state"]) for i in items] == [
        ("rn_a", "r-claude", "serving"),
        ("rn_b", "r-chatgpt", "serving"),
        (None, "r-broken", "halted"),
    ]
    assert items[0]["bindings"] == [{"project": "blizzard", "workspace_id": "ws-1", "agents": 2}]
    assert (items[0]["agents_in_use"], items[0]["max_agents"]) == (0, 2)
    assert items[1]["pause"]["local"] is True
    assert (items[2]["pause"], items[2]["halted_reasons"]) == (None, ["has no id"])


def test_a_served_runner_its_hub_halts_is_listed_halted_naming_why_and_still_served(tmp_path: Path) -> None:
    released = IdentityVerdictHolder("reset")
    released.record(IdentityState.MATCHED, store_runner_id="rn_b", token_runner_id="rn_b", checked_at=_NOW)
    released.stand(HostStanding.NOT_HELD, reason="not held under this host (hs_1) at its hub")
    held = IdentityVerdictHolder("reset")
    held.record(IdentityState.MATCHED, store_runner_id="rn_a", token_runner_id="rn_a", checked_at=_NOW)
    app, _a, _b = _two_runner_app(tmp_path, verdicts=(held, released))
    client = TestClient(app)

    items = client.get("/api/host/runners").json()["items"]

    assert [(i["runner_id"], i["state"], i["served"], i["halted_reasons"]) for i in items] == [
        ("rn_a", "serving", True, []),
        ("rn_b", "halted", True, ["not held under this host (hs_1) at its hub"]),
    ]
    # Its own views still read, so its panel can say why it is halted.
    assert client.get("/runners/rn_b/api/runner").status_code == 200


def test_the_host_registration_read_names_where_the_host_stands(tmp_path: Path) -> None:
    unregistered, _a, _b = _two_runner_app(tmp_path)
    assert TestClient(unregistered).get("/api/host/registration").json() == {
        "state": "unregistered",
        "host_id": None,
        "tenant_id": None,
        "host_name": None,
        "answered_at": None,
        "held_runner_ids": [],
        "detail": None,
        "buffered_facts": 0,
    }

    registration = HostRegistrationHolder.for_host(registered=True)
    registration.hold(
        HostRegistrationStatus(
            HostRegistrationState.REGISTERED, answered_at=_NOW, host_name="host-a", held_runner_ids=("rn_a", "rn_b")
        )
    )
    identity = HostIdentityHolder(HostIdentity("hs_1", "tn_1", _NOW))
    (tmp_path / "registered").mkdir()
    registered, _a, _b = _two_runner_app(tmp_path / "registered", registration=registration, host_identity=identity)
    body = TestClient(registered).get("/api/host/registration").json()
    assert (body["state"], body["host_id"], body["tenant_id"], body["host_name"]) == (
        "registered",
        "hs_1",
        "tn_1",
        "host-a",
    )
    assert body["held_runner_ids"] == ["rn_a", "rn_b"]

    registration.hold(HostRegistrationStatus(HostRegistrationState.NO_HOSTS, detail="404: Not Found"))
    body = TestClient(registered).get("/api/host/registration").json()
    assert (body["state"], body["detail"]) == ("no_hosts", "404: Not Found")


def test_ready_lists_each_runner_beside_the_store_fields(tmp_path: Path) -> None:
    app, _a, _b = _two_runner_app(tmp_path, halted=(HaltedRunner("r-broken", "rn_h", ("x",)),))

    body = TestClient(app).get("/api/ready").json()

    assert {"ready", "store_reachable", "detail"} <= body.keys()
    assert body["runners"] == [
        {"runner_id": "rn_a", "name": "r-claude", "state": "serving"},
        {"runner_id": "rn_b", "name": "r-chatgpt", "state": "serving"},
        {"runner_id": "rn_h", "name": "r-broken", "state": "halted"},
    ]


def test_host_workspaces_lists_each_declared_workspace(tmp_path: Path) -> None:
    app, _a, _b = _two_runner_app(tmp_path)

    assert TestClient(app).get("/api/host/workspaces").json() == {
        "items": [{"workspace_id": "ws-1", "max_environments": 3}]
    }


def test_host_environments_group_by_workspace_naming_each_holders_runner_and_project(tmp_path: Path) -> None:
    app, a_stores, b_stores = _two_runner_app(tmp_path)
    a_stores.environments.record_binding(
        chunk_id="ch_a", environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws-1", project_id="pr_1"
    )
    b_stores.environments.record_binding(
        chunk_id="ch_b", environment_id="e3", workdir="/ws/e3", bound_at=_NOW + timedelta(minutes=1)
    )

    (workspace,) = TestClient(app).get("/api/host/environments").json()["workspaces"]

    assert workspace["workspace_id"] == "ws-1"
    assert workspace["items"] == [
        {
            "environment_id": "e1",
            "chunk_id": "ch_a",
            "held_since": _NOW.isoformat(),
            "held": True,
            "runner_id": "rn_a",
            "project": "pr_1",
        },
        {
            "environment_id": "e2",
            "chunk_id": None,
            "held_since": None,
            "held": False,
            "runner_id": None,
            "project": None,
        },
        {
            "environment_id": "e3",
            "chunk_id": "ch_b",
            "held_since": (_NOW + timedelta(minutes=1)).isoformat(),
            "held": True,
            "runner_id": "rn_b",
            "project": None,
        },
    ]


def test_a_released_binding_reverts_its_slot_to_free(tmp_path: Path) -> None:
    app, a_stores, _b = _two_runner_app(tmp_path)
    a_stores.environments.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    a_stores.environments.record_release(chunk_id="ch_1", environment_id="e1", released_at=_NOW + timedelta(minutes=1))

    (workspace,) = TestClient(app).get("/api/host/environments").json()["workspaces"]

    assert [(i["environment_id"], i["held"]) for i in workspace["items"]] == [
        ("e1", False),
        ("e2", False),
        ("e3", False),
    ]


def test_a_held_binding_naming_an_undeclared_workspace_is_listed_under_its_runners_home(tmp_path: Path) -> None:
    app, a_stores, _b = _two_runner_app(tmp_path)
    a_stores.environments.record_binding(
        chunk_id="ch_1", environment_id="e2", workdir="/ws/e2", bound_at=_NOW, workspace_id="ws-gone"
    )

    (workspace,) = TestClient(app).get("/api/host/environments").json()["workspaces"]

    assert workspace["workspace_id"] == "ws-1"
    assert [(i["environment_id"], i["chunk_id"]) for i in workspace["items"]] == [
        ("e1", None),
        ("e2", "ch_1"),
        ("e3", None),
    ]


def test_a_held_binding_off_the_pool_or_doubled_on_a_slot_still_surfaces(tmp_path: Path) -> None:
    """A binding on an id no longer in the (resized) pool, or a second one on the same slot after a crash/requeue
    race, still holds a chunk — neither collapses nor vanishes."""
    app, a_stores, b_stores = _two_runner_app(tmp_path)
    a_stores.environments.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    b_stores.environments.record_binding(chunk_id="ch_2", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    a_stores.environments.record_binding(chunk_id="ch_9", environment_id="e9", workdir="/ws/e9", bound_at=_NOW)

    (workspace,) = TestClient(app).get("/api/host/environments").json()["workspaces"]

    assert [(i["environment_id"], i["chunk_id"], i["runner_id"]) for i in workspace["items"]] == [
        ("e1", "ch_1", "rn_a"),
        ("e1", "ch_2", "rn_b"),
        ("e2", None, None),
        ("e3", None, None),
        ("e9", "ch_9", "rn_a"),
    ]


def test_a_workers_runner_url_is_its_runners_base_under_the_host() -> None:
    config = RunnerConfig(root=Path("."), db_url="sqlite://", host="127.0.0.1", port=8431, runner_id="rn_a")

    assert config.local_api_url == "http://127.0.0.1:8431/runners/rn_a"
