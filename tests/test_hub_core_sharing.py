"""``build_hub_core``'s members are the very instances every service and the work-source
registry share (component tier) — identity, not equality, so no second copy hides behind
an equal-looking one."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.composition import build_hub_core
from blizzard.hub.store.errors import HubStoreError
from blizzard.hub.tenancy import StoreScope
from tests.support import build_hub, default_tenant

pytestmark = pytest.mark.component


def test_services_share_the_cores_stores(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    services = hub.services

    assert services.findings is services.finding_exit._repo
    assert services.garden_proposals is services.garden_proposal_authoring._proposals
    assert services.garden_proposal_closures is services.garden_proposal_authoring._closures
    assert services.delete is services.garden_proposal_closure._items._delete  # type: ignore[attr-defined]
    assert services.registry is services.claim._registry
    assert services.registry is services.fleet._registry
    assert services.registry is services.claim._exclusive._registry  # type: ignore[attr-defined]


def test_the_garden_proposal_closure_and_the_hub_source_share_one_edit_service(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    hub_editor = hub.work_sources.editor("hub")
    assert hub_editor is not None

    assert hub_editor._edits is hub.services.garden_proposal_closure._items  # type: ignore[attr-defined]


def test_the_cores_tenant_connections_are_scoped_to_the_stores_only_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    core = build_hub_core(hub.engine, clock=hub.clock)

    assert core.tenant_connections.scope == StoreScope(default_tenant(hub.engine))


def test_the_core_names_its_tenant_on_first_use_so_a_stale_store_still_builds(tmp_path: Path) -> None:
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'empty.db'}")

    core = build_hub_core(engine, clock=SystemClock())

    with pytest.raises(HubStoreError):
        _ = core.tenant_connections.scope
