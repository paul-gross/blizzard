"""A retirement sweep may only release the route and lifecycle generation it observed."""

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

from blizzard.hub.domain.chunk.model import Chunk, RouteReleaseCause
from blizzard.hub.domain.execution.detach import DetachService
from blizzard.hub.domain.runners.registration import IWriteRunnerRegistry, RunnerAddition, RunnerRegistration
from blizzard.hub.domain.runners.route import Route
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from tests.support import build_hub, hub_store_connections, seed_chunk_record, seed_runner
from tests.test_load_all_facts_store import _T0, _store
from tests.test_runner_retirement import _bearer, _enroll, _register

pytestmark = pytest.mark.component


@pytest.mark.parametrize("host_call", [False, True])
def test_stale_retirement_retry_cannot_revoke_rescued_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host_call: bool
) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    host = hub.client.post("/api/hosts", json={"name": "host"}).json()
    assert (
        hub.client.post(f"/api/hosts/{host['host_id']}/adoptions", json={"runner_ids": ["runner-a"]}).status_code == 200
    )
    assert hub.client.post("/api/runners/runner-a/retire", json={"by": "op", "force": True}).status_code == 200
    registry = cast(IWriteRunnerRegistry, hub.services.registry)
    original = registry.record_retirement
    tokens: list[str] = []

    def rescue_then_retire(
        runner_id: str,
        *,
        at: datetime,
        by: str,
        force: bool,
        expected_registration: RunnerRegistration | None = None,
        expected_host_id: str | None = None,
        expected_host_retired: bool = True,
    ) -> tuple[int | None, int | None, int] | None:
        monkeypatch.setattr(registry, "record_retirement", original)
        assert hub.client.post("/api/runners/runner-a/reinstate", json={"by": "op"}).status_code == 200
        tokens.append(_enroll(hub))
        return original(
            runner_id,
            at=at,
            by=by,
            force=force,
            expected_registration=expected_registration,
            expected_host_id=expected_host_id,
            expected_host_retired=expected_host_retired,
        )

    monkeypatch.setattr(registry, "record_retirement", rescue_then_retire)
    if host_call:
        response = hub.client.post(
            "/api/fleet/host/runners/runner-a/retire", json={"force": True}, headers=_bearer(host["token"])
        )
    else:
        response = hub.client.post("/api/runners/runner-a/retire", json={"by": "op", "force": True})
    assert response.status_code == 409, response.text
    runner = registry.get_runner("runner-a")
    assert runner is not None and not runner.retired
    assert hub.client.get("/api/fleet/identity", headers=_bearer(tokens[0])).status_code == 200


def test_retired_host_sweep_skips_changed_runner_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path)
    host_id = hub.client.post("/api/hosts", json={"name": "host"}).json()["host_id"]
    seed_runner(hub, "runner")
    hub.services.hosts.adopt(host_id, ["runner"])  # type: ignore[attr-defined]
    registry = cast(IWriteRunnerRegistry, hub.services.registry)
    original = registry.get_runners

    def retire_after_snapshot(ids: Iterable[str]):  # type: ignore[no-untyped-def]
        snapshot = original(ids)
        monkeypatch.setattr(registry, "get_runners", original)
        result = registry.record_retirement("runner", at=hub.clock.now(), by="other", force=True)
        assert result is not None
        return snapshot

    monkeypatch.setattr(registry, "get_runners", retire_after_snapshot)
    response = hub.client.post(f"/api/hosts/{host_id}/retire", json={"by": "op", "force": True})
    assert response.status_code == 200, response.text
    runner = registry.get_runner("runner")
    assert runner is not None and runner.retired
    assert response.json()["released_chunk_ids"] == []


@pytest.mark.parametrize("new_retirement", [False, True])
def test_reinstatement_fences_an_older_sweep(tmp_path: Path, new_retirement: bool) -> None:
    chunks, engine, clock = _store(tmp_path)
    runners = RunnerRegistryStore(hub_store_connections(engine))
    runners.add(RunnerAddition("runner", "runner", "hash", _T0, "test"))
    chunk = Chunk(chunk_id="chunk", graph_id="gr_1", work_refs=[], minted_at=_T0)
    seed_chunk_record(chunks, chunk)
    route = Route(chunk_id="chunk", runner_id="runner", workspace_id="w", environment_ids=[], created_at=_T0)
    with chunks.exclusive.locked(["chunk"]) as handle:
        chunks.route.record_route_locked(handle, route, token_hash="hash", at=_T0)
    old_route = chunks.route.route_of("chunk")
    assert old_route is not None
    retirement = runners.record_retirement("runner", at=_T0, by="test", force=True)
    assert retirement is not None
    _, _, generation = retirement
    runners.record_lifecycle("runner", retired=False, at=_T0, by="test", expected_generation=generation)
    if new_retirement:
        runners.record_retirement("runner", at=_T0, by="test", force=True)
    detach = DetachService(route=chunks.route, exclusive=chunks.exclusive, clock=clock)

    assert detach.release_held(chunk, runner_id="runner", expected_route=old_route, retirement_id=generation) is None
    assert chunks.route.route_of("chunk") == old_route


def test_same_runner_reclaim_is_not_the_route_seen_by_old_sweep(tmp_path: Path) -> None:
    chunks, engine, clock = _store(tmp_path)
    runners = RunnerRegistryStore(hub_store_connections(engine))
    runners.add(RunnerAddition("runner", "runner", "hash", _T0, "test"))
    chunk = Chunk(chunk_id="chunk", graph_id="gr_1", work_refs=[], minted_at=_T0)
    seed_chunk_record(chunks, chunk)
    route = Route(chunk_id="chunk", runner_id="runner", workspace_id="w", environment_ids=[], created_at=_T0)
    with chunks.exclusive.locked(["chunk"]) as handle:
        chunks.route.record_route_locked(handle, route, token_hash="first", at=_T0)
    old = chunks.route.route_of("chunk")
    assert old is not None
    retirement = runners.record_retirement("runner", at=_T0, by="test", force=True)
    assert retirement is not None
    _, _, generation = retirement
    runners.record_lifecycle("runner", retired=False, at=_T0, by="test", expected_generation=generation)
    with chunks.exclusive.locked(["chunk"]) as handle:
        chunks.route.record_route_released_locked(handle, "chunk", at=_T0, cause=RouteReleaseCause.DETACH)
        chunks.route.record_route_locked(handle, route, token_hash="second", at=_T0)
    current = chunks.route.route_of("chunk")
    assert current is not None and current.route_id != old.route_id
    detach = DetachService(route=chunks.route, exclusive=chunks.exclusive, clock=clock)

    assert detach.release_held(chunk, runner_id="runner", expected_route=old, retirement_id=generation) is None
    # A sweep whose roster read happened after the reclaim sees the new route instead.
    assert detach.release_held(chunk, runner_id="runner", expected_route=current, retirement_id=generation) is None
    assert chunks.route.route_of("chunk") == current


def test_host_sweep_uses_roster_at_retirement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path)
    host_id = hub.client.post("/api/hosts", json={"name": "host"}).json()["host_id"]
    seed_runner(hub, "runner")
    hosts = hub.services.hosts
    original = hosts.record_retirement_with_roster  # type: ignore[attr-defined]

    def adopt_then_release(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        # Adoption loses the service's initial roster read, but wins the retirement decision.
        hosts.adopt(host_id, ["runner"])  # type: ignore[attr-defined]
        result = original(*args, **kwargs)
        return result

    monkeypatch.setattr(hosts, "record_retirement_with_roster", adopt_then_release)
    response = hub.client.post(f"/api/hosts/{host_id}/retire", json={"by": "test", "force": True})

    assert response.status_code == 200, response.text
    runner = hub.services.registry.get_runner("runner")
    assert runner is not None and runner.host_id == host_id and runner.retired


def test_host_retirement_retry_after_crash_and_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path)
    host_id = hub.client.post("/api/hosts", json={"name": "host"}).json()["host_id"]
    seed_runner(hub, "runner")
    hosts = hub.services.hosts
    hosts.adopt(host_id, ["runner"])  # type: ignore[attr-defined]
    original = hosts.record_retirement_with_roster  # type: ignore[attr-defined]

    def crash_after_commit(*args: object, **kwargs: object) -> None:
        original(*args, **kwargs)
        raise RuntimeError("crash after host retirement")

    monkeypatch.setattr(hosts, "record_retirement_with_roster", crash_after_commit)
    with pytest.raises(RuntimeError, match="crash after host retirement"):
        hub.client.post(f"/api/hosts/{host_id}/retire", json={"by": "test", "force": True})
    monkeypatch.setattr(hosts, "record_retirement_with_roster", original)
    refused = hub.client.post(f"/api/hosts/{host_id}/releases", json={"runner_ids": ["runner"], "by": "test"})
    assert refused.status_code == 409, refused.text

    response = hub.client.post(f"/api/hosts/{host_id}/retire", json={"by": "test", "force": True})
    assert response.status_code == 200, response.text
    runner = hub.services.registry.get_runner("runner")
    assert runner is not None and runner.host_id == host_id and runner.retired
    refused = hub.client.post("/api/runners/runner/reinstate", json={"by": "test"})
    assert refused.status_code == 409, refused.text
    released = hub.client.post(f"/api/hosts/{host_id}/releases", json={"runner_ids": ["runner"], "by": "test"})
    assert released.status_code == 200, released.text
    rescued = hub.client.post("/api/runners/runner/reinstate", json={"by": "test"})
    assert rescued.status_code == 200, rescued.text
    retry = hub.client.post(f"/api/hosts/{host_id}/retire", json={"by": "test", "force": True})
    assert retry.status_code == 200, retry.text
    runner = hub.services.registry.get_runner("runner")
    assert runner is not None and runner.host_id is None and not runner.retired


def test_stale_host_sweep_does_not_retire_rescued_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path)
    host_id = hub.client.post("/api/hosts", json={"name": "host"}).json()["host_id"]
    seed_runner(hub, "runner")
    hosts = hub.services.hosts
    hosts.adopt(host_id, ["runner"])  # type: ignore[attr-defined]
    registry = hub.services.registry
    original = registry.get_runners

    def rescue_after_snapshot(ids: Iterable[str]):  # type: ignore[no-untyped-def]
        snapshot = original(ids)
        monkeypatch.setattr(registry, "get_runners", original)
        retired = hub.client.post(f"/api/hosts/{host_id}/retire", json={"by": "other", "force": True})
        assert retired.status_code == 200, retired.text
        released = hub.client.post(f"/api/hosts/{host_id}/releases", json={"runner_ids": ["runner"], "by": "test"})
        assert released.status_code == 200, released.text
        rescued = hub.client.post("/api/runners/runner/reinstate", json={"by": "test"})
        assert rescued.status_code == 200, rescued.text
        return snapshot

    monkeypatch.setattr(registry, "get_runners", rescue_after_snapshot)
    response = hub.client.post(f"/api/hosts/{host_id}/retire", json={"by": "stale", "force": True})
    assert response.status_code == 200, response.text
    runner = registry.get_runner("runner")
    assert runner is not None and runner.host_id is None and not runner.retired
    assert response.json()["released_chunk_ids"] == []


@pytest.mark.parametrize("force", [False, True])
def test_host_retire_cannot_act_on_runner_adopted_after_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, force: bool
) -> None:
    hub = build_hub(tmp_path)
    first = hub.client.post("/api/hosts", json={"name": "first"}).json()
    second = hub.client.post("/api/hosts", json={"name": "second"}).json()
    added = hub.client.post(
        "/api/fleet/host/runners", json={"name": "runner"}, headers={"Authorization": f"Bearer {first['token']}"}
    ).json()
    runner_id = added["runner_id"]
    original = hub.services.fleet.retire

    def move_then_retire(  # type: ignore[no-untyped-def]
        registration: RunnerRegistration, *, by: str, force: bool, expected_host_id: str, expected_host_retired: bool
    ):
        released = hub.client.post(
            f"/api/hosts/{first['host_id']}/releases", json={"runner_ids": [runner_id], "by": "test"}
        )
        assert released.status_code == 200, released.text
        adopted = hub.client.post(f"/api/hosts/{second['host_id']}/adoptions", json={"runner_ids": [runner_id]})
        assert adopted.status_code == 200, adopted.text
        return original(
            registration,
            by=by,
            force=force,
            expected_host_id=expected_host_id,
            expected_host_retired=expected_host_retired,
        )

    monkeypatch.setattr(hub.services.fleet, "retire", move_then_retire)
    response = hub.client.post(
        f"/api/fleet/host/runners/{runner_id}/retire",
        json={"force": force},
        headers={"Authorization": f"Bearer {first['token']}"},
    )
    assert response.status_code == 404, response.text
    runner = hub.services.registry.get_runner(runner_id)
    assert runner is not None and runner.host_id == second["host_id"] and not runner.retired
    assert runner.token_hash is not None
