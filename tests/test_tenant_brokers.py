"""``TenantBrokers`` holds one event broker per tenant, created on first use and forgotten when
dropped; each broker's ring and ids are its own, so one tenant's churn never reaches another's
replay tail (unit tier)."""

from __future__ import annotations

import pytest

from blizzard.hub.events.broker import EventBroker
from blizzard.hub.events.brokers import TenantBrokers
from blizzard.hub.tenancy import TenantId

pytestmark = pytest.mark.unit

_A = TenantId("ten_A")
_B = TenantId("ten_B")


def test_a_tenant_has_one_broker_created_on_first_use() -> None:
    brokers = TenantBrokers()

    first = brokers.for_tenant(_A)

    assert brokers.for_tenant(_A) is first
    assert brokers.for_tenant(_B) is not first


def test_a_dropped_tenants_next_use_creates_a_fresh_broker() -> None:
    brokers = TenantBrokers()
    before = brokers.for_tenant(_A)
    before.publish_queue_changed()

    brokers.drop(_A)

    after = brokers.for_tenant(_A)
    assert after is not before
    assert after.replay_since(0) == []


def test_an_adopted_broker_is_the_tenants_unless_it_already_has_one() -> None:
    brokers = TenantBrokers()
    made_early = EventBroker()

    assert brokers.adopt(_A, made_early) is made_early
    assert brokers.for_tenant(_A) is made_early
    assert brokers.adopt(_A, EventBroker()) is made_early


def test_one_tenants_churn_never_evicts_anothers_replay_tail() -> None:
    brokers = TenantBrokers()
    quiet = brokers.for_tenant(_A)
    quiet_id = quiet.publish_queue_changed()

    busy = brokers.for_tenant(_B)
    for _ in range(1000):
        busy.publish_queue_changed()

    assert [event.id for event in quiet.replay_since(0)] == [quiet_id]
    assert quiet.latest_id() == quiet_id
    # Ids count within a tenant: the busy tenant's ids say nothing of the quiet one's.
    assert quiet.replay_since(quiet_id) == []
    assert busy.latest_id() == 1000
