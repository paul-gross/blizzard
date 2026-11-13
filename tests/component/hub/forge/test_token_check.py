"""The hub-start check of undeclared tokens over a migrated store: a fine-grained token behind a policy reading checks
is reported, a classic one is not, and a declared variant is left to the operator (component tier)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import update

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.landing_policy import LandingPolicy
from blizzard.foundation.secrets.factories import secret_cipher
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.composition import TenantStores, build_hub_core, build_live_config
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.credentials import CredentialDraft
from blizzard.hub.domain.config.repositories import RepositoryDraft
from blizzard.hub.domain.config.secrets import SecretName
from blizzard.hub.forge.auth import IForgeAuthorizations
from blizzard.hub.forge.token_check import UndeclaredToken, undeclared_tokens
from blizzard.hub.secrets import hub_key_provider
from blizzard.hub.store.schema import repositories
from blizzard.hub.tenancy import StoreScope
from tests.support.scaffolding import OP, config_authoring, default_tenant, migrated_hub_at, seed_forge

_NOW = datetime(2026, 11, 13, 12, 0, 0, tzinfo=UTC)


class _World:
    def __init__(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path / 'hub.db'}"
        migrated_hub_at(db_url)
        self.engine = create_engine_from_url(db_url)
        keys = hub_key_provider({}, data_dir=tmp_path / "data")
        clock = FixedClock(_NOW)
        self.stores: TenantStores = build_hub_core(self.engine, clock=clock).open(
            StoreScope(default_tenant(self.engine))
        )
        self.authoring: ConfigAuthoring = config_authoring(self.engine, keys=keys, clock=clock)
        self.forge_id = seed_forge(self.engine, "http://forge.fixture")
        self.authorizations: IForgeAuthorizations = build_live_config(
            self.stores, cipher=secret_cipher(keys)
        ).authorizations

    def repository(self, repo: str, secret: str, policy: LandingPolicy = LandingPolicy.MERGE_ON_CHECKS) -> None:
        draft = RepositoryDraft(owner="acme", repo=repo, base_branch="main", secret_name=secret, landing_policy=policy)
        self.authoring.create_repository(repo, None, draft.on(self.forge_id), OP)

    def secret(self, name: str, value: str) -> None:
        self.authoring.create_secret(SecretName.parse(name), value, OP)

    def act_with_credential(self, repo: str, secret: str, settings: dict[str, object]) -> None:
        """Point ``repo`` at a ``github.token`` credential over ``secret``; no verb writes ``credential_id`` yet."""
        credential = self.authoring.create_credential(
            CredentialDraft(
                slug="deploy",
                name=None,
                forge_id=self.forge_id,
                binding="github.token",
                secret_name=secret,
                settings=settings,
            ),
            OP,
        )
        with self.engine.begin() as conn:
            conn.execute(
                update(repositories)
                .where(repositories.c.tenant_id == default_tenant(self.engine), repositories.c.repo == repo)
                .values(credential_id=credential.credential_id)
            )

    def check(self) -> list[UndeclaredToken]:
        return undeclared_tokens(
            authorizations=self.authorizations,
            repositories=self.stores.repository_records,
            credentials=self.stores.credential_records,
        )


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def test_an_undeclared_fine_grained_secret_behind_merge_on_checks_is_reported_and_a_classic_one_is_not(
    world: _World,
) -> None:
    world.secret("fine", "github_pat_fine")
    world.secret("classic", "ghp_classic")
    world.repository("widget", "fine")
    world.repository("gadget", "classic")
    world.repository("parked", "fine", LandingPolicy.PARK)

    assert world.check() == [
        UndeclaredToken(
            credential=None, secret="fine", variant="fine-grained", withheld=("checks.read",), repositories=("widget",)
        )
    ]


def test_an_undeclared_fine_grained_credential_is_reported_by_its_slug(world: _World) -> None:
    world.secret("gh", "ghp_classic")
    world.secret("deploy", "github_pat_fine")
    world.repository("widget", "gh")
    world.act_with_credential("widget", "deploy", {})

    (token,) = world.check()
    assert (token.credential, token.secret, token.variant) == ("deploy", "deploy", "fine-grained")


def test_a_credential_declaring_its_variant_is_not_reported(world: _World) -> None:
    world.secret("gh", "ghp_classic")
    world.secret("deploy", "github_pat_fine")
    world.repository("widget", "gh")
    world.act_with_credential("widget", "deploy", {"variant": "fine-grained"})

    assert world.check() == []
