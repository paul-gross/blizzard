"""``ConfigObjectCache`` over the composition's revisions read and secret reader (component tier).

Records are written through ``ConfigAuthoring`` against a migrated-to-head sqlite store; the
built object is a stand-in client holding the record's fields and its revealed token."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, update

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.secrets.factories import secret_cipher
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.composition import LiveConfig, build_hub_core, build_live_config
from blizzard.hub.domain.config.changes import RecordKind
from blizzard.hub.domain.config.credentials import (
    ConfiguredCredential,
    CredentialDraft,
    CredentialEdit,
    resolve_credential,
)
from blizzard.hub.domain.config.forge_resolution import ForgeChoice
from blizzard.hub.domain.config.secrets import SecretName
from blizzard.hub.domain.config.work_sources import WorkSourceDraft, WorkSourceEdit
from blizzard.hub.forge.auth import ForgeScope
from blizzard.hub.live_config import CacheKey
from blizzard.hub.secrets import hub_key_provider
from blizzard.hub.store.internal.forge_record_store import ForgeRecordStore
from blizzard.hub.store.schema import work_sources
from blizzard.hub.tenancy import StoreScope
from tests.support.forge_auth import sent_token
from tests.support.scaffolding import (
    OP,
    config_authoring,
    default_tenant,
    hub_store_connections,
    migrated_hub_at,
    seed_forge,
)

_NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
_FIELDS = WorkSourceDraft(provider="github", locator="acme/demo", annotate=False, secret="gh")


@dataclass
class _Client:
    locator: str
    annotate: bool
    token: str
    closed: bool = field(default=False)

    def close(self) -> None:
        self.closed = True


class _World:
    def __init__(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path / 'hub.db'}"
        migrated_hub_at(db_url)
        self.engine = create_engine_from_url(db_url)
        self.keys = hub_key_provider({}, data_dir=tmp_path / "data")
        clock = FixedClock(_NOW)
        self.core = build_hub_core(self.engine, clock=clock)
        self.stores = self.core.open(StoreScope(default_tenant(self.engine)))
        self.authoring = config_authoring(self.engine, keys=self.keys, clock=clock)
        self.authoring.create_secret(SecretName.parse("gh"), "tok-a", OP)
        self.source_id = self.authoring.create_work_source_naming(
            "demo", None, _FIELDS, _on_github(self.engine), OP
        ).record.work_source_id
        self.live = self.second_process()
        self.builds = 0

    def second_process(self) -> LiveConfig:
        return build_live_config(self.stores, cipher=secret_cipher(self.keys))

    def client(self, live: LiveConfig | None = None) -> _Client | None:
        live = live or self.live

        def build() -> _Client:
            self.builds += 1
            record = self.stores.work_source_records.get(self.source_id)
            credential = resolve_credential(record, self.stores.credential_records) if record is not None else None
            assert record is not None and credential is not None
            token = sent_token(live.authorizations.authorization(credential, ForgeScope.WORK_SOURCE))
            return _Client(record.fields.locator, record.fields.annotate, token)

        return live.objects.get(RecordKind.WORK_SOURCE, self.source_id, build)

    def key(self) -> CacheKey:
        revisions = self.live.objects.revisions_for(RecordKind.WORK_SOURCE, [self.source_id])[self.source_id]
        return CacheKey.of(RecordKind.WORK_SOURCE, self.source_id, revisions)

    def act_with_credential(self) -> ConfiguredCredential:
        """Point the work source at a new credential reading its own secret; no verb writes ``credential_id`` yet."""
        self.authoring.create_secret(SecretName.parse("deploy"), "tok-deploy", OP)
        credential = self.authoring.create_credential(
            CredentialDraft(
                slug="deploy",
                name=None,
                forge_id=self.source().fields.forge_id,
                binding="github.token",
                secret_name="deploy",
                settings={},
            ),
            OP,
        )
        with self.engine.begin() as conn:
            conn.execute(
                update(work_sources)
                .where(
                    work_sources.c.tenant_id == default_tenant(self.engine),
                    work_sources.c.work_source_id == self.source_id,
                )
                .values(credential_id=credential.credential_id)
            )
        assert self.source().credential_id == credential.credential_id
        return credential

    def source(self):
        record = self.stores.work_source_records.get(self.source_id)
        assert record is not None
        return record


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def test_a_hit_reuses_the_built_object(world: _World) -> None:
    first = world.client()
    assert world.client() is first
    assert world.builds == 1
    assert first is not None and not first.closed


def test_a_record_edit_rebuilds_and_sets_the_replaced_object_aside(world: _World) -> None:
    first = world.client()
    world.authoring.edit_work_source(world.source(), WorkSourceEdit(annotate=True), OP)

    second = world.client()

    assert first is not None and not first.closed
    assert second is not None and second.annotate and not second.closed


def test_a_replaced_object_is_closed_by_the_replacement_after_next(world: _World) -> None:
    first = world.client()
    world.authoring.edit_work_source(world.source(), WorkSourceEdit(annotate=True), OP)
    world.client()
    assert first is not None and not first.closed
    world.authoring.edit_work_source(world.source(), WorkSourceEdit(annotate=False), OP)

    world.client()

    assert first.closed


def test_a_retire_rebuilds_and_sets_the_replaced_object_aside(world: _World) -> None:
    first = world.client()
    world.authoring.retire_work_source(world.source(), OP)

    second = world.client()

    assert first is not None and not first.closed
    assert second is not None and second is not first


def test_a_secret_replace_rebuilds_with_the_new_value(world: _World) -> None:
    first = world.client()
    secret = world.stores.secrets.get("gh")
    assert secret is not None
    world.authoring.replace_secret(secret, "tok-b", OP)

    second = world.client()

    assert first is not None and not first.closed and first.token == "tok-a"
    assert second is not None and second.token == "tok-b"


def test_a_cold_cache_and_a_second_cache_build_equivalent_objects(world: _World) -> None:
    world.client()
    world.authoring.edit_work_source(world.source(), WorkSourceEdit(locator="acme/moved"), OP)
    warm = world.client()

    other = world.client(world.second_process())

    assert warm is not None and other is not None
    assert (other.locator, other.annotate, other.token) == (warm.locator, warm.annotate, warm.token)


def test_an_unknown_record_builds_nothing(world: _World) -> None:
    assert world.live.objects.get(RecordKind.REPOSITORY, "absent", lambda: _Client("x", False, "t")) is None


def _on_github(engine: Engine) -> ForgeChoice:
    """The forge choice naming the forge holding GitHub's API origin, made when none does."""
    forge = ForgeRecordStore(hub_store_connections(engine)).get(seed_forge(engine, "https://api.github.com"))
    assert forge is not None
    return ForgeChoice(forge=forge.forge_id, named=forge)


def test_an_implicit_referrer_keys_on_its_own_secret_and_no_credential(world: _World) -> None:
    key = world.key()

    assert (key.secret_name, key.secret_revision, key.credential_id, key.credential_revision) == ("gh", 1, None, None)


def test_a_credential_referrer_keys_on_the_credential_and_its_secret(world: _World) -> None:
    credential = world.act_with_credential()

    key = world.key()

    assert (key.secret_name, key.credential_id, key.credential_revision) == ("deploy", credential.credential_id, 1)


def test_a_credential_edit_changes_a_referrer_s_key(world: _World) -> None:
    credential = world.act_with_credential()
    before = world.key()

    world.authoring.edit_credential(credential, CredentialEdit(settings={"variant": "classic"}), OP)

    after = world.key()
    assert after != before and after.credential_revision == 2


def test_a_credential_secret_replace_changes_a_referrer_s_key(world: _World) -> None:
    world.act_with_credential()
    before = world.key()
    secret = world.stores.secrets.get("deploy")
    assert secret is not None

    world.authoring.replace_secret(secret, "tok-deploy-2", OP)

    after = world.key()
    assert before.secret_revision is not None
    assert after != before and after.secret_revision == before.secret_revision + 1
