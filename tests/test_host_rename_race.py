"""A host's rename is checked for a taken name inside its own write (component tier), not only on the earlier read."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from blizzard.hub.domain.runners.host import (
    HostAddition,
    HostLifecycleFact,
    HostNameTaken,
    HostReport,
    HostRetired,
    HostTokenRotation,
)
from blizzard.hub.domain.runners.host_report import HostReportService
from blizzard.hub.domain.runners.registration import RunnerAddition, RunnerRetired
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from tests.support import build_hub

pytestmark = pytest.mark.component


def test_a_rename_onto_a_name_taken_after_the_domain_check_is_refused_in_the_write(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ids: dict[str, Any] = {n: hub.client.post("/api/hosts", json={"name": n}).json() for n in ("one", "two")}
    store = cast(HostRegistryStore, hub.services.hosts)
    report = HostReport(name="one", url=None, redirect_uris=(), workspaces=(), subscriptions=())

    # The domain's read saw "one" free for ``two`` only before ``one`` was added; the write must look again.
    with pytest.raises(HostNameTaken):
        store.record_report(ids["two"]["host_id"], report, name="one", at=datetime(2026, 10, 20, tzinfo=UTC))
    two = store.get_host(ids["two"]["host_id"])
    assert two is not None and two.name == "two" and two.last_seen_at is None
    assert store.live_names(excluding=ids["two"]["host_id"]) == {"one"}


class _StaleNames:
    """The real store, except the domain's name read is stale: it saw every name free."""

    def __init__(self, store: HostRegistryStore) -> None:
        self._store = store

    def live_names(self, *, excluding: str | None = None) -> frozenset[str]:
        return frozenset()

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._store, attr)


def test_a_report_that_loses_the_rename_race_still_lands_as_a_heartbeat(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ids: dict[str, Any] = {n: hub.client.post("/api/hosts", json={"name": n}).json() for n in ("one", "two")}
    store = cast(HostRegistryStore, hub.services.hosts)
    service = HostReportService(hosts=cast(Any, _StaleNames(store)), clock=hub.clock)
    held = store.get_host(ids["two"]["host_id"])
    assert held is not None
    report = HostReport(name="one", url="http://two", redirect_uris=(), workspaces=(), subscriptions=())

    reported = service.report(held, report)

    assert reported.name == "two"
    after = store.get_host(ids["two"]["host_id"])
    assert after is not None and after.name == "two" and after.last_seen_at is not None


def test_stale_host_writes_refuse_after_retirement_and_retirement_is_idempotent(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host_id = hub.client.post("/api/hosts", json={"name": "one"}).json()["host_id"]
    store = cast(HostRegistryStore, hub.services.hosts)
    now = datetime(2026, 10, 20, tzinfo=UTC)
    fact = HostLifecycleFact(host_id, True, now, "test")
    first, _ = store.record_retirement(host_id, fact=fact, at=now, by="test")
    second, _ = store.record_retirement(host_id, fact=fact, at=now, by="test")
    assert first is not None and second is None
    with pytest.raises(HostRetired):
        store.adopt(host_id, ())
    with pytest.raises(HostRetired):
        store.record_report(host_id, HostReport("new", None, (), (), ()), name="new", at=now)
    with pytest.raises(HostRetired):
        store.rotate_token(HostTokenRotation(host_id, "new-hash", now, "test"))
    host = store.get_host(host_id)
    assert host is not None and host.retired and host.name == "one" and host.token_hash is None


def test_competing_host_additions_and_retirements_have_one_winner(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    store = cast(HostRegistryStore, hub.services.hosts)
    now = datetime(2026, 10, 20, tzinfo=UTC)

    def add(number: int) -> bool:
        try:
            store.add(HostAddition(f"host-{number}", "same", f"hash-{number}", now, "test"))
        except HostNameTaken:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as actors:
        assert sorted(actors.map(add, (1, 2))) == [False, True]
    hosts = store.list_hosts()
    assert len(hosts) == 1
    fact = HostLifecycleFact(hosts[0].host_id, True, now, "test")
    with ThreadPoolExecutor(max_workers=2) as actors:
        results = list(
            actors.map(lambda _: store.record_retirement(fact.host_id, fact=fact, at=now, by="test"), range(2))
        )
    assert sum(fact_id is not None for fact_id, _ in results) == 1
    assert sum(revocation_id is not None for _, revocation_id in results) == 1


def test_stale_runner_placement_refuses_retired_host_or_runner(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hosts = cast(HostRegistryStore, hub.services.hosts)
    runners = cast(RunnerRegistryStore, hub.services.registry)
    now = datetime(2026, 10, 20, tzinfo=UTC)
    hosts.add(HostAddition("host-one", "one", "host-hash", now, "test"))
    runners.add(RunnerAddition("runner-one", "one", "runner-hash", now, "test"))
    runners.record_lifecycle("runner-one", retired=True, at=now, by="test")
    with pytest.raises(RunnerRetired):
        hosts.adopt("host-one", ("runner-one",))
    hosts.record_retirement("host-one", fact=HostLifecycleFact("host-one", True, now, "test"), at=now, by="test")
    with pytest.raises(HostRetired):
        runners.add(RunnerAddition("runner-two", "two", "hash-two", now, "test", host_id="host-one"))
    assert runners.get_runner("runner-two") is None
