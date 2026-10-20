"""A host's rename is checked for a taken name inside its own write (component tier), not only on the earlier read."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from blizzard.hub.domain.runners.host import HostNameTaken, HostReport
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
    assert store.record_report(ids["two"]["host_id"], report, name="two", at=datetime(2026, 10, 20, tzinfo=UTC))
