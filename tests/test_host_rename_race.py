"""A host's rename is checked for a taken name inside its own write (component tier), not only on the earlier read."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from blizzard.hub.domain.runners.host import HostNameTaken, HostReport
from blizzard.hub.domain.runners.host_report import HostReportService
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
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
