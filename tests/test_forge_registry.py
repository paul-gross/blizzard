"""``ForgeRegistry`` over a migrated store, the tenant's ``ConfigObjectCache``, and the hub's ``ActionLoop``: an adapter
superseded by an edit closes on the action loop, a hub shut down closes its cached adapters before the loop stops, and
a role refuses an operation its forge does not offer, and every role is admitted against its forge key's rate budget at
its caller's priority (component tier).

The adapters are real ``GitHubLanding``s over a recorded ``httpx2`` transport that notes the thread it is closed on."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.composition import LiveConfig, TenantStores, build_hub_core, build_live_config
from blizzard.hub.delivery.action_loop import ActionLoop
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.forges import FORGE_KIND_CAPABILITIES, ConfiguredForge, ForgeEdit
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryDraft,
    RepositoryEdit,
    RepositoryId,
    RepositorySecretUnavailable,
)
from blizzard.hub.domain.config.secrets import SecretName
from blizzard.hub.forge.budget import BudgetKey, ForgeBudgetLow, ForgeBudgets, ForgePriority
from blizzard.hub.forge.internal.github_landing import GitHubLanding
from blizzard.hub.forge.landing import ForgeCapabilityMissing, ForgeRateLimited, MergeMethod
from blizzard.hub.forge.registry import ForgeBinding, ForgeRegistry, TurnCutOff
from blizzard.hub.secrets import hub_key_provider, secret_cipher
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.tenancy import StoreScope
from tests.support import (
    OP,
    build_hub,
    config_authoring,
    default_tenant,
    hub_store_connections,
    migrated_hub_at,
    seed_forge,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
_DRAFT = RepositoryDraft(owner="acme", repo="widget", base_branch="main", secret_name="gh")
_PULL = {
    "number": 7,
    "state": "open",
    "mergeable_state": "clean",
    "head": {"ref": "feat", "sha": "h1"},
    "base": {"ref": "main", "sha": "b1"},
}


class _Transport(httpx2.MockTransport):
    """Answers every pull request read with :data:`_PULL` — once ``release`` is set, when ``hold`` is — and records
    every request and the thread it is closed on."""

    def __init__(self, *, hold: bool = False) -> None:
        super().__init__(self._answer)
        self.hold = hold
        self.started = threading.Event()
        self.release = threading.Event()
        self.requests: list[httpx2.Request] = []
        self.closed_on: str | None = None
        self.status = 200
        self.headers: dict[str, str] = {}

    async def _answer(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        self.started.set()
        while self.hold and not self.release.is_set():
            await asyncio.sleep(0.01)
        return httpx2.Response(self.status, headers=self.headers, content=json.dumps(_PULL).encode())

    async def aclose(self) -> None:
        self.closed_on = threading.current_thread().name


def _closed(transport: _Transport) -> str | None:
    """The thread ``transport`` was closed on, waiting a few seconds for a close scheduled onto another loop."""
    deadline = time.monotonic() + 5
    while transport.closed_on is None and time.monotonic() < deadline:
        time.sleep(0.01)
    return transport.closed_on


class _World:
    def __init__(self, tmp_path: Path, capabilities: Mapping[str, frozenset[Capability]]) -> None:
        db_url = f"sqlite:///{tmp_path / 'hub.db'}"
        migrated_hub_at(db_url)
        engine = create_engine_from_url(db_url)
        keys = hub_key_provider({}, data_dir=tmp_path / "data")
        clock = FixedClock(_NOW)
        self.stores = build_hub_core(engine, clock=clock).open(StoreScope(default_tenant(engine)))
        self.authoring = config_authoring(engine, keys=keys, clock=clock)
        self.authoring.create_secret(SecretName.parse("gh"), "tok", OP)
        self.forge_id = seed_forge(engine, "http://forge.fixture")
        self.repository = self.authoring.create_repository("widget", None, _DRAFT.on(self.forge_id), OP).repository_id
        live = build_live_config(self.stores, cipher=secret_cipher(keys))
        self.transports: list[_Transport] = []
        self.hold_next = False
        self.next_headers: dict[str, str] = {}
        self.budgets = ForgeBudgets(clock=clock)
        self.key = BudgetKey(self.forge_id, "gh")
        self.registry = ForgeRegistry(
            records=self.stores.repository_records,
            forges=self.stores.forge_records,
            objects=live.objects,
            secrets=live.secrets,
            build=self._build,
            capabilities=capabilities,
            budgets=self.budgets,
        )

    def _build(self, binding: ForgeBinding) -> GitHubLanding:
        transport = _Transport(hold=self.hold_next)
        transport.headers = self.next_headers
        self.transports.append(transport)
        return GitHubLanding(binding, clock=FixedClock(_NOW), transport=transport)

    def record(self) -> ConfiguredRepository:
        record = self.stores.repository_records.get(self.repository)
        assert record is not None
        return record

    def edit(self, base_branch: str) -> None:
        self.authoring.edit_repository(self.record(), RepositoryEdit(base_branch=base_branch), OP)

    def forge(self) -> ConfiguredForge:
        forge = self.stores.forge_records.get(self.forge_id)
        assert forge is not None
        return forge


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path, FORGE_KIND_CAPABILITIES)


@pytest.fixture
def loop() -> Iterator[ActionLoop]:
    action_loop = ActionLoop()
    yield action_loop
    action_loop.stop()


def test_an_adapter_superseded_by_an_edit_closes_on_the_action_loop_after_its_pass_finishes(
    world: _World, loop: ActionLoop
) -> None:
    world.hold_next = True
    holding = world.registry.changes(world.repository, priority=ForgePriority.LANDING)
    in_flight = loop.submit(holding.change(7))
    (first,) = world.transports
    assert first.started.wait(5)

    world.hold_next = False
    world.edit("dev")
    world.registry.changes(world.repository, priority=ForgePriority.LANDING)
    first.release.set()

    assert in_flight.result(5).number == 7
    assert first.closed_on is None
    world.edit("main")
    world.registry.changes(world.repository, priority=ForgePriority.LANDING)
    assert _closed(first) == "hub-action-loop"


def test_a_forge_edit_rebuilds_the_adapter_on_its_new_address(world: _World, loop: ActionLoop) -> None:
    loop.submit(world.registry.changes(world.repository, priority=ForgePriority.LANDING).change(7)).result(5)
    world.authoring.edit_forge(world.forge(), ForgeEdit(api_base="http://forge2.fixture"), OP)
    loop.submit(world.registry.changes(world.repository, priority=ForgePriority.LANDING).change(7)).result(5)

    assert len(world.transports) == 2
    assert str(world.transports[1].requests[0].url).startswith("http://forge2.fixture/")


def test_an_unchanged_record_hands_out_the_same_adapter(world: _World, loop: ActionLoop) -> None:
    loop.submit(world.registry.changes(world.repository, priority=ForgePriority.LANDING).change(7)).result(5)
    loop.submit(
        world.registry.repository_forge(
            world.repository, cut_off=TurnCutOff(), priority=ForgePriority.LANDING
        ).changes.change(7)
    ).result(5)

    assert len(world.transports) == 1 and len(world.transports[0].requests) == 2


def test_a_hub_shut_down_closes_its_cached_adapters_before_the_action_loop_stops(tmp_path: Path) -> None:
    transport = _Transport()

    def forges(stores: TenantStores, live: LiveConfig, budgets: ForgeBudgets) -> ForgeRegistry:
        return ForgeRegistry(
            records=stores.repository_records,
            forges=stores.forge_records,
            objects=live.objects,
            secrets=live.secrets,
            build=lambda binding: GitHubLanding(binding, clock=FixedClock(_NOW), transport=transport),
            capabilities=FORGE_KIND_CAPABILITIES,
            budgets=budgets,
        )

    hub = build_hub(tmp_path, forges=forges)
    record = RepositoryRecordStore(hub_store_connections(hub.engine)).resolve("acme-widget")
    assert record is not None and hub.app is not None

    with TestClient(hub.app):
        forge = hub.services.forges.changes(record.repository_id, priority=ForgePriority.LANDING)
        assert hub.hub_services.action_loop.submit(forge.change(7)).result(5).number == 7
        assert transport.closed_on is None

    assert transport.closed_on == "hub-action-loop"


def test_a_role_refuses_an_operation_its_forge_does_not_offer(tmp_path: Path, loop: ActionLoop) -> None:
    offered = frozenset({Capability.CHANGES_READ})
    world = _World(tmp_path, {"github": offered})
    forge = world.registry.repository_forge(world.repository, cut_off=TurnCutOff(), priority=ForgePriority.LANDING)

    assert loop.submit(forge.changes.change(7)).result(5).number == 7
    refusals = [
        (forge.changes.merge(7, "h1", MergeMethod.SQUASH, "msg"), Capability.CHANGES_MERGE_SQUASH),
        (forge.checks.rerun_check("11"), Capability.CHECKS_RERUN),
        (forge.refs.branch_tip("main"), Capability.REFS_READ),
        (forge.refs.advance_ref("main", "a", "b"), Capability.REFS_ADVANCE),
        (forge.refs.merge_into("main", "b", "msg"), Capability.BRANCHES_MERGE),
        (forge.refs.create_ref("main", "b"), Capability.REFS_CREATE),
        (forge.refs.delete_ref("main"), Capability.REFS_DELETE),
        (forge.checks.change_checks(7), Capability.CHECKS_READ),
    ]
    for call, capability in refusals:
        with pytest.raises(ForgeCapabilityMissing) as raised:
            loop.submit(call).result(5)
        assert raised.value.capability is capability

    assert forge.offered == offered
    assert len(world.transports[0].requests) == 1


def test_recently_closed_needs_the_read_capability(tmp_path: Path, loop: ActionLoop) -> None:
    world = _World(tmp_path, {"github": frozenset({Capability.REFS_READ})})
    forge = world.registry.repository_forge(world.repository, cut_off=TurnCutOff(), priority=ForgePriority.LANDING)

    with pytest.raises(ForgeCapabilityMissing) as raised:
        loop.submit(forge.changes.recently_closed("main", 5)).result(5)

    assert raised.value.capability is Capability.CHANGES_READ


def test_offered_answers_the_kinds_set_for_a_forge_alone_or_a_repository(tmp_path: Path) -> None:
    offered = frozenset({Capability.REFS_READ, Capability.COMPARE})
    world = _World(tmp_path, {"github": offered})

    assert world.registry.offered(world.forge()) == offered
    assert world.registry.offered(world.forge(), RepositoryId(world.repository)) == offered


def test_a_secret_that_no_longer_reveals_refuses_the_build(world: _World) -> None:
    world.authoring.retire_repository(world.record(), OP)
    secret = world.stores.secrets.get("gh")
    assert secret is not None
    world.authoring.retire_secret(secret, OP)

    with pytest.raises(RepositorySecretUnavailable):
        world.registry.refs(world.repository, priority=ForgePriority.LANDING)


def test_a_retired_repository_is_still_built(world: _World, loop: ActionLoop) -> None:
    world.authoring.retire_repository(world.record(), OP)

    assert (
        loop.submit(world.registry.changes(world.repository, priority=ForgePriority.LANDING).change(7)).result(5).number
        == 7
    )


def _rate(remaining: int, *, limit: int = 100, reset_after: int = 600) -> dict[str, str]:
    return {
        "x-ratelimit-limit": str(limit),
        "x-ratelimit-remaining": str(remaining),
        "x-ratelimit-reset": str(int((_NOW + timedelta(seconds=reset_after)).timestamp())),
    }


def test_an_answer_teaches_the_forge_keys_budget(world: _World, loop: ActionLoop) -> None:
    world.next_headers = _rate(70)
    loop.submit(world.registry.changes(world.repository, priority=ForgePriority.LANDING).change(7)).result(5)

    (reading,) = world.budgets.readings(world.key)
    assert (reading.bucket, reading.remaining, reading.limit) == ("core", 70, 100)


def test_a_turned_away_call_makes_no_request_while_a_landing_proceeds(world: _World, loop: ActionLoop) -> None:
    world.next_headers = _rate(30)
    loop.submit(world.registry.changes(world.repository, priority=ForgePriority.LANDING).change(7)).result(5)
    (transport,) = world.transports

    with pytest.raises(ForgeBudgetLow) as raised:
        loop.submit(world.registry.refs(world.repository, priority=ForgePriority.SWEEPING).branch_tip("main")).result(5)

    assert raised.value.resume_at == _NOW + timedelta(seconds=600)
    assert len(transport.requests) == 1
    loop.submit(world.registry.changes(world.repository, priority=ForgePriority.LANDING).change(7)).result(5)
    assert len(transport.requests) == 2


def test_a_rate_refusal_marks_the_budget_spent_until_it_lifts(world: _World, loop: ActionLoop) -> None:
    world.next_headers = {"retry-after": "120"}
    landing = world.registry.repository_forge(world.repository, cut_off=TurnCutOff(), priority=ForgePriority.LANDING)
    loop.submit(landing.changes.change(7)).result(5)
    (transport,) = world.transports
    transport.status = 429

    with pytest.raises(ForgeRateLimited) as refused:
        loop.submit(landing.changes.change(7)).result(5)
    assert not isinstance(refused.value, ForgeBudgetLow)

    with pytest.raises(ForgeBudgetLow) as turned_away:
        loop.submit(landing.checks.checks("h1")).result(5)
    assert turned_away.value.resume_at == refused.value.resume_at == _NOW + timedelta(seconds=120)
    assert len(transport.requests) == 2


def test_a_rebuilt_adapter_keeps_its_budget(world: _World, loop: ActionLoop) -> None:
    world.next_headers = _rate(10)
    loop.submit(world.registry.changes(world.repository, priority=ForgePriority.LANDING).change(7)).result(5)
    world.next_headers = {}
    world.edit("dev")

    with pytest.raises(ForgeBudgetLow):
        loop.submit(world.registry.changes(world.repository, priority=ForgePriority.SWEEPING).change(7)).result(5)

    assert len(world.transports) == 2 and world.transports[1].requests == []
