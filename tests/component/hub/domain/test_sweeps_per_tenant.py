"""The hub's sweeps across tenants: each is one pass per hub that reads its corpus once and acts on every
row in that row's own tenant, through that tenant's half of the sweep — never one tenant's scope on another
tenant's row, and never on a tenant closed since the pass read its corpus."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from blizzard.hub.store import schema as s
from tests.support.scaffolding import (
    build_hub,
    close_tenant,
    default_tenant,
    hub_scoped,
)
from tests.support.tenant_sweeps import (
    close_events,
    landed_hub_ref,
)

# --- the close drain over two tenants of one hub --------------------------------------------


def test_one_drain_pass_closes_each_tenants_intent_in_its_own_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    carried = default_tenant(hub.engine)
    other = hub.add_tenant("other")
    stores = {
        carried: landed_hub_ref(hub, carried, "ch_carried"),
        other.tenant: landed_hub_ref(hub, other.tenant, "ch_other"),
    }

    hub.hub_services.close_drain.sweep()

    assert stores[carried].delivery.pending_close_intents() == []
    assert stores[other.tenant].delivery.pending_close_intents() == []
    assert close_events(hub, carried) == ["ch_carried"]
    assert close_events(hub, other.tenant) == ["ch_other"]


def test_a_drain_pass_leaves_a_closed_tenants_intent_untouched(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    carried = default_tenant(hub.engine)
    other = hub.add_tenant("other")
    carried_stores = landed_hub_ref(hub, carried, "ch_carried")
    other_stores = landed_hub_ref(hub, other.tenant, "ch_other")
    close_tenant(hub.engine, other.tenant)

    hub.hub_services.close_drain.sweep()

    assert carried_stores.delivery.pending_close_intents() == []
    assert [intent.chunk_id for intent in other_stores.delivery.pending_close_intents()] == ["ch_other"]
    assert close_events(hub, other.tenant) == []
    attempts = s.close_intent_attempts
    with hub.engine.connect() as conn:
        tried = conn.execute(
            hub_scoped(sa.select(sa.func.count()).select_from(attempts).where(attempts.c.tenant_id == other.tenant))
        ).scalar_one()
    assert tried == 0
