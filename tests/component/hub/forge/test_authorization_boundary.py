"""Landing and ingestion behind one forge authorization boundary (component tier), over a migrated store, the tenant's
live configuration, real ``GitHubLanding`` adapters on a recording ``httpx2`` transport, and the production work-source
registry: a replaced secret reaches the next request of either with no restart, every caller acting as one token
shares its budget, every repository acting as it shares its write lock, and a record naming no credential sends its
secret and keys its budget on it as before."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import update

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.secrets.factories import secret_cipher
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.composition import build_hub_core, build_live_config
from blizzard.hub.delivery.action_loop import ActionLoop
from blizzard.hub.domain.config.credential_naming import CredentialId
from blizzard.hub.domain.config.credentials import CredentialDraft
from blizzard.hub.domain.config.forges import FORGE_KIND_CAPABILITIES
from blizzard.hub.domain.config.repositories import RepositoryDraft, RepositoryId
from blizzard.hub.domain.config.secrets import SecretName
from blizzard.hub.domain.config.work_sources import WorkSourceFields, WorkSourceId
from blizzard.hub.forge.auth import ForgeScope, ScopedAuthorizations
from blizzard.hub.forge.budget import BudgetKey, ForgeBudgets, ForgePriority, SweepingGate
from blizzard.hub.forge.internal.github_landing import GitHubLanding
from blizzard.hub.forge.rates import rate_dialect
from blizzard.hub.forge.registry import ForgeBinding, ForgeRegistry, WriteLocks
from blizzard.hub.secrets import hub_key_provider
from blizzard.hub.store.schema import repositories, work_sources
from blizzard.hub.tenancy import StoreScope
from blizzard.hub.work_sources.internal.factory import WorkSourceEntry
from blizzard.hub.work_sources.internal.github_work_source import GitHubWorkSource
from tests.support.forge_auth import sent_header
from tests.support.scaffolding import OP, config_authoring, default_tenant, migrated_hub_at, seed_forge

_NOW = datetime(2026, 11, 13, 12, 0, 0, tzinfo=UTC)
_PULL = {
    "number": 7,
    "state": "open",
    "mergeable_state": "clean",
    "head": {"ref": "feat", "sha": "h1"},
    "base": {"ref": "main", "sha": "b1"},
}


def _rate(remaining: int, *, limit: int = 100) -> dict[str, str]:
    return {
        "x-ratelimit-limit": str(limit),
        "x-ratelimit-remaining": str(remaining),
        "x-ratelimit-reset": str(int((_NOW + timedelta(seconds=600)).timestamp())),
    }


class _Budgets(ForgeBudgets):
    """The tenant's budgets, noting every work-source gate bound from them."""

    def __init__(self) -> None:
        super().__init__(clock=FixedClock(_NOW), dialects=rate_dialect)
        self.gates: list[SweepingGate] = []

    def sweeping(self, forge_id: str, authority: str | None, forge_kind: str) -> SweepingGate:
        gate = super().sweeping(forge_id, authority, forge_kind)
        self.gates.append(gate)
        return gate


class _World:
    def __init__(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path / 'hub.db'}"
        migrated_hub_at(db_url)
        self.engine = create_engine_from_url(db_url)
        keys = hub_key_provider({}, data_dir=tmp_path / "data")
        clock = FixedClock(_NOW)
        core = build_hub_core(self.engine, clock=clock)
        self.stores = core.open(StoreScope(default_tenant(self.engine)))
        self.authoring = config_authoring(self.engine, keys=keys, clock=clock)
        self.authoring.create_secret(SecretName.parse("gh"), "tok-1", OP)
        self.forge_id = seed_forge(self.engine, "http://forge.fixture")
        self.widget = self._repository("widget")
        self.gadget = self._repository("gadget")
        self.source = self.authoring.create_work_source(
            "issues",
            None,
            WorkSourceFields(
                provider="github", locator="acme/issues", forge_id=self.forge_id, annotate=False, secret="gh"
            ),
            OP,
        ).work_source_id
        live = build_live_config(self.stores, cipher=secret_cipher(keys))
        self.requests: list[httpx2.Request] = []
        self.headers: dict[str, str] = {}
        self.bindings: dict[str, ForgeBinding] = {}
        self.budgets = _Budgets()
        self.registry = ForgeRegistry(
            records=self.stores.repository_records,
            forges=self.stores.forge_records,
            credentials=self.stores.credential_records,
            objects=live.objects,
            authorizations=live.authorizations,
            build=self._build,
            capabilities=FORGE_KIND_CAPABILITIES,
            budgets=self.budgets,
            write_locks=WriteLocks(),
            dialects=rate_dialect,
        )
        self.sources = WorkSourceEntry.registry(
            records=self.stores.work_source_records,
            forges=self.stores.forge_records,
            credentials=self.stores.credential_records,
            objects=live.objects,
            authorizations=ScopedAuthorizations(live.authorizations, ForgeScope.WORK_SOURCE),
            users=core.users,
            work_item_store=self.stores.work_item_store,
            edits=self.stores.work_item_edits,
            resolution=self.stores.garden_proposal_resolution,
            budgets=self.budgets,
        )

    def _repository(self, repo: str) -> RepositoryId:
        draft = RepositoryDraft(owner="acme", repo=repo, base_branch="main", secret_name="gh")
        return self.authoring.create_repository(repo, None, draft.on(self.forge_id), OP).repository_id

    async def _answer(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(200, headers=self.headers, content=json.dumps(_PULL).encode())

    def _build(self, binding: ForgeBinding) -> GitHubLanding:
        self.bindings[binding.repo] = binding
        return GitHubLanding(binding, clock=FixedClock(_NOW), transport=httpx2.MockTransport(self._answer))

    def land(self, loop: ActionLoop, repository: RepositoryId) -> httpx2.Request:
        """Read a pull request through ``repository``'s landing role; the request it sent."""
        loop.submit(self.registry.changes(repository, priority=ForgePriority.LANDING).change(7)).result(5)
        return self.requests[-1]

    def ingesting(self) -> GitHubWorkSource:
        source = self.sources.get(WorkSourceId(self.source))
        assert isinstance(source, GitHubWorkSource)
        return source

    def replace(self, name: str, value: str) -> None:
        secret = self.stores.secrets.get(name)
        assert secret is not None
        self.authoring.replace_secret(secret, value, OP)

    def act_with_credential(self) -> CredentialId:
        """Point both repositories and the work source at one credential reading its own secret; no verb writes
        ``credential_id`` yet."""
        self.authoring.create_secret(SecretName.parse("deploy"), "tok-deploy-1", OP)
        credential = self.authoring.create_credential(
            CredentialDraft(
                slug="deploy",
                name=None,
                forge_id=self.forge_id,
                binding="github.token",
                secret_name="deploy",
                settings={"variant": "classic"},
            ),
            OP,
        )
        tenant = default_tenant(self.engine)
        with self.engine.begin() as conn:
            conn.execute(
                update(repositories)
                .where(repositories.c.tenant_id == tenant)
                .values(credential_id=credential.credential_id)
            )
            conn.execute(
                update(work_sources)
                .where(work_sources.c.tenant_id == tenant)
                .values(credential_id=credential.credential_id)
            )
        return credential.credential_id


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


@pytest.fixture
def loop() -> Iterator[ActionLoop]:
    action_loop = ActionLoop()
    yield action_loop
    action_loop.stop()


def test_a_record_naming_no_credential_sends_its_secret_and_keys_its_budget_on_it(
    world: _World, loop: ActionLoop
) -> None:
    world.headers = _rate(70)
    request = world.land(loop, world.widget)

    assert request.headers["Authorization"] == "token tok-1"
    key = BudgetKey(world.forge_id, "secret/gh")
    assert [reading.remaining for reading in world.budgets.readings(key)] == [70]
    assert sent_header(world.ingesting().client.auth) == "token tok-1"
    assert world.budgets.gates[-1].key == key


@pytest.mark.parametrize("credential", [False, True], ids=["implicit", "credential"])
def test_a_replaced_secret_reaches_the_next_landing_and_work_source_request_with_no_restart(
    world: _World, loop: ActionLoop, credential: bool
) -> None:
    secret = "gh"
    if credential:
        world.act_with_credential()
        secret = "deploy"
    world.land(loop, world.widget)
    world.ingesting()

    world.replace(secret, "tok-2")

    assert world.land(loop, world.widget).headers["Authorization"] == "token tok-2"
    assert sent_header(world.ingesting().client.auth) == "token tok-2"


@pytest.mark.parametrize("credential", [False, True], ids=["implicit", "credential"])
def test_a_repository_and_a_work_source_acting_as_one_token_share_one_budget(
    world: _World, loop: ActionLoop, credential: bool
) -> None:
    authority = "secret/gh"
    if credential:
        world.act_with_credential()
        authority = "secret/deploy"
    world.headers = _rate(10)
    world.land(loop, world.widget)

    world.ingesting()
    sweeping = world.budgets.gates[-1]

    assert sweeping.key == BudgetKey(world.forge_id, authority)
    assert sweeping.admit() is not None, "the landing answer's low reading turns the work source's sweep away"


@pytest.mark.parametrize("credential", [False, True], ids=["implicit", "credential"])
def test_two_repositories_acting_as_one_token_share_one_write_lock(
    world: _World, loop: ActionLoop, credential: bool
) -> None:
    if credential:
        world.act_with_credential()
    world.land(loop, world.widget)
    world.land(loop, world.gadget)

    async def locks() -> tuple[asyncio.Lock, asyncio.Lock]:
        return world.bindings["widget"].write_lock(), world.bindings["gadget"].write_lock()

    widget, gadget = loop.submit(locks()).result(5)
    assert widget is gadget
