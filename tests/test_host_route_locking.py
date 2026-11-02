"""A stale claim cannot create a route after its host or runner retires."""

from pathlib import Path

import pytest

import blizzard.hub.store.internal.runner_registry_store as runner_store_module
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.runners.host import (
    HostAddition,
    HostHoldsRoutes,
    HostLifecycleFact,
    HostRetired,
    RunnerNotUnderHost,
)
from blizzard.hub.domain.runners.registration import RunnerAddition, RunnerHoldsRoutes, RunnerRetired, TokenRotation
from blizzard.hub.domain.runners.route import Route
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from tests.support import hub_store_connections, seed_chunk_record
from tests.test_load_all_facts_store import _T0, _store

pytestmark = pytest.mark.component


@pytest.mark.parametrize("retired", ["host", "runner"])
def test_route_write_rechecks_lifecycle(tmp_path: Path, retired: str) -> None:
    chunks, engine, _ = _store(tmp_path)
    scoped = hub_store_connections(engine)
    hosts, runners = HostRegistryStore(scoped), RunnerRegistryStore(scoped)
    hosts.add(HostAddition("host", "host", "hash", _T0, "test"))
    runners.add(RunnerAddition("runner", "runner", "hash", _T0, "test", host_id="host"))
    seed_chunk_record(chunks, Chunk(chunk_id="chunk", graph_id="gr_1", work_refs=[], minted_at=_T0))
    if retired == "host":
        hosts.record_retirement("host", fact=HostLifecycleFact("host", True, _T0, "test"), at=_T0, by="test")
    else:
        runners.record_lifecycle("runner", retired=True, at=_T0, by="test")
    route = Route(chunk_id="chunk", runner_id="runner", workspace_id="w", environment_ids=[], created_at=_T0)
    with (
        pytest.raises(HostRetired if retired == "host" else RunnerRetired),
        chunks.exclusive.locked(["chunk"]) as handle,
    ):
        chunks.route.record_route_locked(handle, route, token_hash="hash", at=_T0)
    assert chunks.route.route_of("chunk") is None


def test_release_rechecks_membership_and_rolls_back_batch(tmp_path: Path) -> None:
    _, engine, _ = _store(tmp_path)
    scoped = hub_store_connections(engine)
    hosts, runners = HostRegistryStore(scoped), RunnerRegistryStore(scoped)
    hosts.add(HostAddition("host", "host", "hash", _T0, "test"))
    runners.add(RunnerAddition("a", "a", "a", _T0, "test", host_id="host"))
    runners.add(RunnerAddition("b", "b", "b", _T0, "test"))
    with pytest.raises(RunnerNotUnderHost):
        hosts.release("host", ["a", "b"])
    runner = runners.get_runner("a")
    assert runner is not None and runner.host_id == "host"


@pytest.mark.parametrize("target", ["host", "runner"])
def test_nonforced_retirement_rechecks_holdings_in_write(tmp_path: Path, target: str) -> None:
    chunks, engine, _ = _store(tmp_path)
    scoped = hub_store_connections(engine)
    hosts, runners = HostRegistryStore(scoped), RunnerRegistryStore(scoped)
    hosts.add(HostAddition("host", "host", "hash", _T0, "test"))
    runners.add(RunnerAddition("runner", "runner", "hash", _T0, "test", host_id="host"))
    seed_chunk_record(chunks, Chunk(chunk_id="chunk", graph_id="gr_1", work_refs=[], minted_at=_T0))
    route = Route(chunk_id="chunk", runner_id="runner", workspace_id="w", environment_ids=["env"], created_at=_T0)
    with chunks.exclusive.locked(["chunk"]) as handle:
        chunks.route.record_route_locked(handle, route, token_hash="hash", at=_T0)
    with pytest.raises(HostHoldsRoutes if target == "host" else RunnerHoldsRoutes) as refusal:
        if target == "host":
            hosts.record_retirement(
                "host", fact=HostLifecycleFact("host", True, _T0, "test"), at=_T0, by="test", force=False
            )
        else:
            runners.record_retirement("runner", at=_T0, by="test", force=False)
    assert "chunk" in str(refusal.value)
    host, runner = hosts.get_host("host"), runners.get_runner("runner")
    assert host is not None and not host.retired and host.token_hash == "hash"
    assert runner is not None and not runner.retired


@pytest.mark.parametrize("target", ["host", "runner"])
def test_stale_runner_writes_and_rekey_refuse_retirement(tmp_path: Path, target: str) -> None:
    chunks, engine, _ = _store(tmp_path)
    scoped = hub_store_connections(engine)
    hosts, runners = HostRegistryStore(scoped), RunnerRegistryStore(scoped)
    hosts.add(HostAddition("host", "host", "host-hash", _T0, "test"))
    runners.add(RunnerAddition("runner", "runner", "runner-hash", _T0, "test", host_id="host"))
    seed_chunk_record(chunks, Chunk(chunk_id="chunk", graph_id="gr_1", work_refs=[], minted_at=_T0))
    route = Route(chunk_id="chunk", runner_id="runner", workspace_id="w", environment_ids=[], created_at=_T0)
    with chunks.exclusive.locked(["chunk"]) as handle:
        chunks.route.record_route_locked(handle, route, token_hash="original", at=_T0)
    if target == "host":
        hosts.record_retirement("host", fact=HostLifecycleFact("host", True, _T0, "test"), at=_T0, by="test")
    else:
        runners.record_lifecycle("runner", retired=True, at=_T0, by="test")
    error = HostRetired if target == "host" else RunnerRetired
    with pytest.raises(error):
        runners.rotate_token(TokenRotation("runner", "new", _T0, "test"))
    with pytest.raises(error):
        runners.touch_last_seen("runner", at=_T0)
    with pytest.raises(error):
        runners.record_registration("runner", workspace_id="w", env_capacity=None, at=_T0)
    with pytest.raises(error), chunks.exclusive.locked(["chunk"]) as handle:
        chunks.route.record_route_token_locked(handle, "chunk", token_hash="new", at=_T0)


def test_runner_retirement_and_token_revocation_commit_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, engine, _ = _store(tmp_path)
    runners = RunnerRegistryStore(hub_store_connections(engine))
    runners.add(RunnerAddition("runner", "runner", "hash", _T0, "test"))
    original = runner_store_module.locked_token_hash

    def fail(*args: object) -> None:
        raise RuntimeError("revocation failed")

    monkeypatch.setattr(runner_store_module, "locked_token_hash", fail)
    with pytest.raises(RuntimeError, match="revocation failed"):
        runners.record_retirement("runner", at=_T0, by="test", force=True)
    runner = runners.get_runner("runner")
    assert runner is not None and not runner.retired and runner.token_hash == "hash"
    monkeypatch.setattr(runner_store_module, "locked_token_hash", original)
    retirement = runners.record_retirement("runner", at=_T0, by="test", force=True)
    assert retirement is not None
    fact, revocation, generation = retirement
    assert fact is not None and revocation is not None
    assert generation == fact
    assert runners.record_retirement("runner", at=_T0, by="test", force=True) == (None, None, generation)
