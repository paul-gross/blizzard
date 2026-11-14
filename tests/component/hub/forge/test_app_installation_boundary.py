"""A ``github.app-installation`` credential behind the forge authorization boundary (component tier), over a migrated
store holding the App's key as a secret, the tenant's live configuration, real ``GitHubLanding`` adapters, and the
production work-source registry, all against a GitHub App's side of GitHub on a mock transport. The binding is made
known to the store for this module alone: two repositories acting as one installation share its budget, its write
lock and its token; landing and the work source share its budget but never a token; a replaced key mints under its
new revision with no restart; and a refused installation read refuses each path's build as that path's refusal."""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import update

from blizzard.foundation.clock import ControlledClock, FixedClock
from blizzard.foundation.secrets.factories import secret_cipher
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.composition import build_hub_core, build_live_config
from blizzard.hub.delivery.action_loop import ActionLoop
from blizzard.hub.domain.config.credential_bindings import (
    CREDENTIAL_BINDINGS,
    CredentialBinding,
    GitHubAppInstallationBinding,
)
from blizzard.hub.domain.config.credentials import CredentialDraft
from blizzard.hub.domain.config.repositories import RepositoryDraft, RepositoryId
from blizzard.hub.domain.config.secrets import SecretName
from blizzard.hub.domain.config.work_sources import WorkSourceFields, WorkSourceId
from blizzard.hub.domain.kernel.forge_refusals import AuthRefusal
from blizzard.hub.forge.auth import ForgeScope, ScopedAuthorizations
from blizzard.hub.forge.budget import BudgetKey, ForgeBudgets, ForgePriority
from blizzard.hub.forge.internal.authorizations import ForgeAuthorizations
from blizzard.hub.forge.internal.github_auth import InstallationTokens
from blizzard.hub.forge.internal.github_landing import GitHubLanding
from blizzard.hub.forge.landing import ForgeAuthRefused
from blizzard.hub.forge.rates import rate_dialect
from blizzard.hub.forge.registry import ForgeBinding, ForgeRegistry, WriteLocks
from blizzard.hub.secrets import StoreSecretReader, hub_key_provider
from blizzard.hub.store.schema import repositories, work_sources
from blizzard.hub.tenancy import StoreScope
from blizzard.hub.work_sources.internal.factory import WorkSourceEntry
from blizzard.hub.work_sources.internal.github_work_source import GitHubWorkSource
from blizzard.hub.work_sources.source import WorkSourceAuthRefused
from tests.support.forge_auth import sent_header
from tests.support.github_app import API_BASE, APP_ID, INSTALLATION_ID, AppGitHub, app_key_pem
from tests.support.scaffolding import OP, config_authoring, default_tenant, migrated_hub_at, seed_forge

_NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
_AUTHORITY = f"app/{APP_ID}/installation/{INSTALLATION_ID}"


@pytest.fixture(autouse=True)
def _app_binding_known(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the App binding one a credential may name, for this module's store and boundary alone."""
    binding = GitHubAppInstallationBinding()
    monkeypatch.setitem(cast(MutableMapping[str, CredentialBinding], CREDENTIAL_BINDINGS), binding.name, binding)


class _World:
    def __init__(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path / 'hub.db'}"
        migrated_hub_at(db_url)
        self.engine = create_engine_from_url(db_url)
        keys = hub_key_provider({}, data_dir=tmp_path / "data")
        store_clock = FixedClock(_NOW)
        core = build_hub_core(self.engine, clock=store_clock)
        self.stores = core.open(StoreScope(default_tenant(self.engine)))
        self.authoring = config_authoring(self.engine, keys=keys, clock=store_clock)
        self.clock = ControlledClock(_NOW)
        self.github = AppGitHub(clock=self.clock, rate_remaining=4000)
        self.authoring.create_secret(SecretName.parse("app-key"), self.github.private_pem, OP)
        self.forge_id = seed_forge(self.engine, API_BASE)
        self.widget = self._repository("widget")
        self.gadget = self._repository("gadget")
        self.source = self.authoring.create_work_source(
            "issues",
            None,
            WorkSourceFields(
                provider="github", locator="acme/widget", forge_id=self.forge_id, annotate=False, secret="app-key"
            ),
            OP,
        ).work_source_id
        self._act_as_the_installation()
        live = build_live_config(self.stores, cipher=secret_cipher(keys))
        authorizations = ForgeAuthorizations(
            secrets=StoreSecretReader(
                catalog=self.stores.secrets, sealed=self.stores.secrets, cipher=secret_cipher(keys)
            ),
            forges=self.stores.forge_records,
            tokens=InstallationTokens(clock=self.clock, transport=self.github.transport),
        )
        self.bindings: dict[str, ForgeBinding] = {}
        self.budgets = ForgeBudgets(clock=self.clock, dialects=rate_dialect)
        self.write_locks = WriteLocks()
        self.registry = ForgeRegistry(
            records=self.stores.repository_records,
            forges=self.stores.forge_records,
            credentials=self.stores.credential_records,
            objects=live.objects,
            authorizations=authorizations,
            build=self._build,
            capabilities={"github": GitHubLanding.OFFERED},
            budgets=self.budgets,
            write_locks=self.write_locks,
            dialects=rate_dialect,
        )
        self.sources = WorkSourceEntry.registry(
            records=self.stores.work_source_records,
            forges=self.stores.forge_records,
            credentials=self.stores.credential_records,
            objects=live.objects,
            authorizations=ScopedAuthorizations(authorizations, ForgeScope.WORK_SOURCE),
            users=core.users,
            work_item_store=self.stores.work_item_store,
            edits=self.stores.work_item_edits,
            resolution=self.stores.garden_proposal_resolution,
            budgets=self.budgets,
        )

    def _repository(self, repo: str) -> RepositoryId:
        draft = RepositoryDraft(owner="acme", repo=repo, base_branch="main", secret_name="app-key")
        return self.authoring.create_repository(repo, None, draft.on(self.forge_id), OP).repository_id

    def _act_as_the_installation(self) -> None:
        """Point both repositories and the work source at one App credential; no verb writes ``credential_id``
        yet."""
        credential = self.authoring.create_credential(
            CredentialDraft(
                slug="app",
                name=None,
                forge_id=self.forge_id,
                binding="github.app-installation",
                secret_name="app-key",
                settings={"app_id": APP_ID, "installation_id": INSTALLATION_ID},
            ),
            OP,
        )
        assert credential.authority == _AUTHORITY
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

    def _build(self, binding: ForgeBinding) -> GitHubLanding:
        self.bindings[binding.repo] = binding
        return GitHubLanding(binding, clock=self.clock, transport=self.github.transport)

    def land(self, loop: ActionLoop, repository: RepositoryId) -> str:
        """Read the base branch's tip through ``repository``'s landing role; the token it sent."""
        loop.submit(self.registry.refs(repository, priority=ForgePriority.LANDING).branch_tip("main")).result(5)
        return self.github.sent[-1]

    def ingest(self) -> str:
        """The ``Authorization`` header the work source's client puts on a request."""
        source = self.sources.get(WorkSourceId(self.source))
        assert isinstance(source, GitHubWorkSource)
        header = sent_header(source.client.auth)
        assert header is not None
        return header


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


@pytest.fixture
def loop() -> Iterator[ActionLoop]:
    action_loop = ActionLoop()
    yield action_loop
    action_loop.stop()


def test_two_repositories_under_one_installation_share_its_budget_its_write_lock_and_its_token(
    world: _World, loop: ActionLoop
) -> None:
    widget_token = world.land(loop, world.widget)
    gadget_token = world.land(loop, world.gadget)

    widget, gadget = world.bindings["widget"], world.bindings["gadget"]
    assert widget.authorization.authority == gadget.authorization.authority == _AUTHORITY
    assert widget.write_lock() is gadget.write_lock() is world.write_locks.lock(world.forge_id, _AUTHORITY)
    assert widget_token == gadget_token and len(world.github.mints) == 1
    keys = [key for key, _reading in world.budgets.forge_readings(world.forge_id)]
    assert keys == [BudgetKey(world.forge_id, _AUTHORITY)]


def test_landing_and_the_work_source_share_the_installation_budget_but_never_a_token(
    world: _World, loop: ActionLoop
) -> None:
    landing = world.land(loop, world.widget)
    ingesting = world.ingest()

    assert landing != ingesting and len(world.github.mints) == 2
    assert world.bindings["widget"].authorization.authority == _AUTHORITY


def test_a_replaced_key_mints_under_its_new_revision_with_no_restart(world: _World, loop: ActionLoop) -> None:
    before = world.land(loop, world.widget)
    secret = world.stores.secrets.get("app-key")
    assert secret is not None

    world.authoring.replace_secret(secret, world.github.private_pem, OP)
    after = world.land(loop, world.widget)

    assert before != after and len(world.github.mints) == 2 and world.github.reads == 2


def test_a_key_github_does_not_hold_refuses_the_landing_build_as_key_rejected(world: _World) -> None:
    secret = world.stores.secrets.get("app-key")
    assert secret is not None
    world.authoring.replace_secret(secret, app_key_pem(1), OP)

    with pytest.raises(ForgeAuthRefused) as refused:
        world.registry.refs(world.widget, priority=ForgePriority.LANDING)

    assert refused.value.refusal is AuthRefusal.KEY_REJECTED
    assert refused.value.diagnostic.credential == "app"


def test_a_suspended_installation_refuses_the_work_source_build(world: _World) -> None:
    world.github.suspend()

    with pytest.raises(WorkSourceAuthRefused) as refused:
        world.sources.get(WorkSourceId(world.source))

    assert refused.value.refusal is AuthRefusal.INSTALLATION_SUSPENDED
    assert "Unsuspend the App's installation." in str(refused.value)
