"""The host and runner registries' lifecycle versions (component tier): each entity carries the id of its newest
lifecycle fact, every roster and lifecycle write lands only on the versions it was decided on and writes nothing on a
stale one, and each joins a supplied unit of work."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.hub.domain.runners.host import (
    Host,
    HostAddition,
    HostLifecycleConflict,
    HostLifecycleFact,
    RunnerNotUnderHost,
)
from blizzard.hub.domain.runners.lifecycle_version import HostLifecycleVersion
from blizzard.hub.domain.runners.registration import (
    RunnerAddition,
    RunnerLifecycleConflict,
    RunnerRegistration,
)
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.transactions import HubTransactions
from tests.support import build_hub, hub_store_connections

pytestmark = pytest.mark.component

_AT = datetime(2026, 11, 1, tzinfo=UTC)
_V0 = HostLifecycleVersion("hs_a", 0)


class _Rollback(Exception):
    pass


@pytest.fixture
def stores(tmp_path: Path) -> tuple[HostRegistryStore, RunnerRegistryStore, HubTransactions]:
    connections = hub_store_connections(build_hub(tmp_path).engine)
    hosts = HostRegistryStore(connections)
    runners = RunnerRegistryStore(connections)
    hosts.add(HostAddition("hs_a", "box", "host-hash", at=_AT, by="op"))
    runners.add(RunnerAddition("rn_a", "a", "runner-hash", at=_AT, by="op"))
    return hosts, runners, HubTransactions(connections)


def _host(hosts: HostRegistryStore) -> Host:
    host = hosts.get_host("hs_a")
    assert host is not None
    return host


def _runner(runners: RunnerRegistryStore) -> RunnerRegistration:
    runner = runners.get_runner("rn_a")
    assert runner is not None
    return runner


def _retire_host(hosts: HostRegistryStore, *, expected: int) -> int:
    fact_id, _ = hosts.record_retirement(
        HostLifecycleVersion("hs_a", expected), fact=HostLifecycleFact("hs_a", True, _AT, "op"), at=_AT, by="op"
    )
    assert fact_id is not None
    return fact_id


def test_each_entity_carries_its_newest_lifecycle_fact_id_or_zero(
    stores: tuple[HostRegistryStore, RunnerRegistryStore, HubTransactions],
) -> None:
    hosts, runners, _ = stores
    assert _host(hosts).lifecycle_version == 0
    assert _runner(runners).lifecycle_version == 0

    host_fact = _retire_host(hosts, expected=0)
    retired = runners.record_lifecycle("rn_a", retired=True, at=_AT, by="op", expected_lifecycle=0)
    reinstated = runners.record_lifecycle("rn_a", retired=False, at=_AT, by="op", expected_lifecycle=retired)

    assert hosts.get_hosts(["hs_a"])["hs_a"].lifecycle_version == host_fact
    assert hosts.list_hosts(include_retired=True)[0].lifecycle_version == host_fact
    assert runners.get_runners(["rn_a"])["rn_a"].lifecycle_version == reinstated
    assert runners.list_runners()[0].lifecycle_version == reinstated


def test_a_stale_host_version_refuses_each_write_and_writes_nothing(
    stores: tuple[HostRegistryStore, RunnerRegistryStore, HubTransactions],
) -> None:
    hosts, runners, _ = stores
    hosts.adopt(_V0, ["rn_a"], expected_runners={"rn_a": 0})
    _retire_host(hosts, expected=0)

    with pytest.raises(HostLifecycleConflict):
        hosts.release(_V0, ["rn_a"], expected_runners={"rn_a": 0})
    with pytest.raises(HostLifecycleConflict):
        _retire_host(hosts, expected=0)
    with pytest.raises(HostLifecycleConflict):
        runners.add(
            RunnerAddition("rn_b", "b", "hash-b", at=_AT, by="op", host_id="hs_a"),
            expected_host=_V0,
        )
    with pytest.raises(HostLifecycleConflict):
        runners.record_lifecycle("rn_a", retired=True, at=_AT, by="op", expected_lifecycle=0, expected_host=_V0)

    assert _runner(runners).host_id == "hs_a"
    assert not _runner(runners).retired
    assert runners.get_runner("rn_b") is None


def test_a_stale_runner_version_refuses_each_write_and_writes_nothing(
    stores: tuple[HostRegistryStore, RunnerRegistryStore, HubTransactions],
) -> None:
    hosts, runners, _ = stores
    runners.record_lifecycle("rn_a", retired=True, at=_AT, by="op", expected_lifecycle=0)

    with pytest.raises(RunnerLifecycleConflict):
        hosts.adopt(_V0, ["rn_a"], expected_runners={"rn_a": 0})
    with pytest.raises(RunnerLifecycleConflict):
        hosts.add(
            HostAddition("hs_b", "other", "hash-b", at=_AT, by="op", adopt=("rn_a",)), expected_runners={"rn_a": 0}
        )
    with pytest.raises(RunnerLifecycleConflict):
        runners.record_lifecycle("rn_a", retired=False, at=_AT, by="op", expected_lifecycle=0)

    assert _runner(runners).host_id is None
    assert _runner(runners).retired
    assert hosts.get_host("hs_b") is None


def test_an_addition_under_a_host_must_carry_that_hosts_version(
    stores: tuple[HostRegistryStore, RunnerRegistryStore, HubTransactions],
) -> None:
    _, runners, _ = stores
    under = RunnerAddition("rn_b", "b", "hash-b", at=_AT, by="op", host_id="hs_a")

    with pytest.raises(ValueError, match="hs_a"):
        runners.add(under)
    with pytest.raises(ValueError, match="hs_a"):
        runners.add(under, expected_host=HostLifecycleVersion("hs_other", 0))


def test_each_write_joins_a_supplied_unit_and_rolls_back_with_it(
    stores: tuple[HostRegistryStore, RunnerRegistryStore, HubTransactions],
) -> None:
    hosts, runners, transactions = stores

    with pytest.raises(_Rollback), transactions.begin() as unit:
        hosts.adopt(_V0, ["rn_a"], expected_runners={"rn_a": 0}, transaction=unit)
        hosts.release(_V0, ["rn_a"], expected_runners={"rn_a": 0}, transaction=unit)
        hosts.add(HostAddition("hs_b", "other", "hash-b", at=_AT, by="op"), transaction=unit)
        hosts.record_retirement(_V0, fact=HostLifecycleFact("hs_a", True, _AT, "op"), at=_AT, by="op", transaction=unit)
        runners.add(RunnerAddition("rn_c", "c", "hash-c", at=_AT, by="op"), transaction=unit)
        runners.record_lifecycle("rn_a", retired=True, at=_AT, by="op", expected_lifecycle=0, transaction=unit)
        raise _Rollback

    host = hosts.get_host("hs_a")
    runner = runners.get_runner("rn_a")
    assert host is not None and not host.retired and host.lifecycle_version == 0
    assert runner is not None and not runner.retired and runner.host_id is None
    assert hosts.get_host("hs_b") is None and runners.get_runner("rn_c") is None


def test_an_add_adopting_a_runner_without_its_version_is_refused_by_name(
    stores: tuple[HostRegistryStore, RunnerRegistryStore, HubTransactions],
) -> None:
    hosts, _, _ = stores

    with pytest.raises(ValueError, match="rn_a"):
        hosts.add(HostAddition("hs_b", "other", "hash-b", at=_AT, by="op", adopt=("rn_a",)))
    assert hosts.get_host("hs_b") is None


def test_a_release_naming_a_runner_no_longer_under_the_host_releases_none(
    stores: tuple[HostRegistryStore, RunnerRegistryStore, HubTransactions],
) -> None:
    hosts, runners, _ = stores
    runners.add(RunnerAddition("rn_b", "b", "hash-b", at=_AT, by="op"))
    hosts.adopt(_V0, ["rn_a"], expected_runners={"rn_a": 0})

    with pytest.raises(RunnerNotUnderHost):
        hosts.release(_V0, ["rn_a", "rn_b"], expected_runners={"rn_a": 0, "rn_b": 0})
    assert _runner(runners).host_id == "hs_a"
