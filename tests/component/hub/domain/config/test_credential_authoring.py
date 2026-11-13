"""Credentials over the real stores: the four credential verbs and their change rows, their refusals, a forge or a
secret retire refused while an enabled credential names it, and every credential read and write kept to its own tenant
(component tier, migrated sqlite-on-disk)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, Door, FieldChange, RecordKind, RecordRef
from blizzard.hub.domain.config.credential_bindings import CredentialSettingsInvalid
from blizzard.hub.domain.config.credentials import (
    ConfiguredCredential,
    CredentialAuthorityTaken,
    CredentialBindingImmutable,
    CredentialBindingUnknown,
    CredentialDraft,
    CredentialEdit,
    CredentialForgeKindMismatch,
    CredentialInUse,
    CredentialSecretUnavailable,
    CredentialSlug,
    CredentialSlugTaken,
)
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeFields, ForgeId, ForgeInUse, ForgeUnavailable
from blizzard.hub.domain.config.repositories import RepositoryFields
from blizzard.hub.domain.config.secrets import SecretName, SecretReferenced
from blizzard.hub.secrets import hub_key_provider
from blizzard.hub.store.errors import HubStoreError
from blizzard.hub.store.internal.config_change_store import ConfigChangeStore
from blizzard.hub.store.internal.credential_record_store import CredentialRecordStore
from blizzard.hub.store.schema import credentials
from blizzard.hub.tenancy import StoreScope, TenantId
from tests.support.scaffolding import (
    OP,
    build_hub,
    config_authoring,
    hub_scoped,
    hub_store_connections,
    migrated_hub_at,
    scoped_connections,
)

_NOW = datetime(2026, 11, 13, 12, 0, 0, tzinfo=UTC)
_CLI = ChangeContext(actor="alice", door=Door.CLI)
_GHE = "https://ghe.acme.internal/api/v3"


def _draft(forge_id: ForgeId, *, slug: str = "deploy", secret: str = "gh", **settings: object) -> CredentialDraft:
    return CredentialDraft(
        slug=slug, name=None, forge_id=forge_id, binding="github.token", secret_name=secret, settings=settings
    )


class _World:
    def __init__(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path / 'hub.db'}"
        migrated_hub_at(db_url)
        self.engine: Engine = create_engine_from_url(db_url)
        connections = hub_store_connections(self.engine)
        self.credentials = CredentialRecordStore(connections)
        self.log = ConfigChangeStore(connections)
        keys = hub_key_provider({}, data_dir=tmp_path / "data")
        self.authoring: ConfigAuthoring = config_authoring(self.engine, keys=keys, clock=FixedClock(_NOW))
        self.authoring.create_secret(SecretName.parse("gh"), "tok", OP)
        self.authoring.create_secret(SecretName.parse("gh2"), "tok2", OP)
        self.forge: ConfiguredForge = self.authoring.create_forge(
            "ghe", "Acme GHE", "github", ForgeFields(_GHE, None), _CLI
        )

    def credential(self, *, slug: str = "deploy", secret: str = "gh", **settings: object) -> ConfiguredCredential:
        return self.authoring.create_credential(_draft(self.forge.forge_id, slug=slug, secret=secret, **settings), _CLI)

    def name_from_repository(self, credential: ConfiguredCredential) -> str:
        """A repository naming ``credential`` by its ``credential_id``, which no write sets yet."""
        repo = self.authoring.create_repository(
            "demo",
            None,
            RepositoryFields(
                forge_id=self.forge.forge_id, owner="acme", repo="demo", base_branch="main", secret_name="gh"
            ),
            _CLI,
        )
        with self.engine.begin() as conn:
            conn.execute(
                sa.text("UPDATE repositories SET credential_id = :credential WHERE repository_id = :repository"),
                {"credential": credential.credential_id, "repository": repo.repository_id},
            )
        return repo.repository_id


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def test_a_credential_is_created_edited_retired_and_enabled_each_with_one_change_row_by_id(world: _World) -> None:
    created = world.credential(variant="classic")
    assert world.credentials.resolve("deploy") == created
    assert created.authority == "secret/gh"
    edited = world.authoring.edit_credential(created, CredentialEdit(slug="deploy-bot", secret_name="gh2"), _CLI)
    assert (edited.authority, edited.revision) == ("secret/gh2", 2)
    assert world.credentials.resolve("deploy") == edited
    retired = world.authoring.retire_credential(edited, _CLI)
    assert world.credentials.get(created.credential_id) == retired and retired.retired
    assert world.authoring.retire_credential(retired, _CLI) is retired
    enabled = world.authoring.enable_credential(retired, _CLI)
    assert world.credentials.get(created.credential_id) == enabled and not enabled.retired

    rows = world.log.page(before=None, limit=10, record_kind=RecordKind.CREDENTIAL, record_key=created.credential_id)
    assert [(row.op, row.revision, row.record_key) for row in reversed(rows)] == [
        (ChangeOp.CREATE, 1, created.credential_id),
        (ChangeOp.EDIT, 2, created.credential_id),
        (ChangeOp.RETIRE, 3, created.credential_id),
        (ChangeOp.ENABLE, 4, created.credential_id),
    ]
    assert rows[2].diff == (FieldChange("slug", "deploy", "deploy-bot"), FieldChange("secret", "gh", "gh2"))
    assert all(row.project_id is None for row in rows)


def test_an_unknown_binding_is_refused(world: _World) -> None:
    draft = CredentialDraft("deploy", None, world.forge.forge_id, "github.oauth", "gh", {})
    with pytest.raises(CredentialBindingUnknown) as caught:
        world.authoring.create_credential(draft, _CLI)
    assert caught.value.code == "credential_binding_unknown"
    assert world.credentials.list_all(include_retired=True) == []


def test_a_binding_for_another_forge_kind_is_refused(world: _World) -> None:
    with world.engine.begin() as conn:
        conn.execute(sa.text("UPDATE forges SET kind = 'gitlab' WHERE forge_id = :id"), {"id": world.forge.forge_id})
    with pytest.raises(CredentialForgeKindMismatch) as caught:
        world.credential()
    assert caught.value.code == "credential_forge_kind_mismatch"


def test_settings_the_binding_refuses_are_refused_naming_the_setting(world: _World) -> None:
    with pytest.raises(CredentialSettingsInvalid) as caught:
        world.credential(variant="enterprise")
    assert (caught.value.code, caught.value.field) == ("credential_settings_invalid", "settings.variant")
    with pytest.raises(CredentialSettingsInvalid, match=r"settings\.variant"):
        world.authoring.edit_credential(world.credential(), CredentialEdit(settings={"variant": "x"}), _CLI)


def test_a_forge_or_binding_on_an_edit_is_refused(world: _World) -> None:
    credential = world.credential()
    with pytest.raises(CredentialBindingImmutable) as caught:
        world.authoring.edit_credential(credential, CredentialEdit(binding="github.token"), _CLI)
    assert caught.value.code == "credential_binding_immutable"
    with pytest.raises(CredentialBindingImmutable):
        world.authoring.edit_credential(credential, CredentialEdit(forge_id=world.forge.forge_id), _CLI)
    assert world.credentials.get(credential.credential_id) == credential


def test_an_authority_another_credential_holds_is_refused_naming_it(world: _World) -> None:
    world.credential()
    with pytest.raises(CredentialAuthorityTaken, match="credential deploy") as caught:
        world.credential(slug="other")
    assert (caught.value.code, caught.value.holder) == ("credential_authority_taken", "deploy")
    other = world.credential(slug="other", secret="gh2")
    with pytest.raises(CredentialAuthorityTaken, match="credential deploy"):
        world.authoring.edit_credential(other, CredentialEdit(secret_name="gh"), _CLI)
    with pytest.raises(CredentialSlugTaken):
        world.authoring.edit_credential(other, CredentialEdit(slug="deploy"), _CLI)


def test_a_retired_holder_keeps_its_authority(world: _World) -> None:
    world.authoring.retire_credential(world.credential(), _CLI)
    with pytest.raises(CredentialAuthorityTaken, match="credential deploy"):
        world.credential(slug="other")


def test_a_credential_an_enabled_record_names_is_refused_retirement_naming_it(world: _World) -> None:
    credential = world.credential()
    repository_id = world.name_from_repository(credential)
    with pytest.raises(CredentialInUse) as caught:
        world.authoring.retire_credential(credential, _CLI)
    assert caught.value.code == "credential_in_use"
    assert caught.value.referrers == [RecordRef(RecordKind.REPOSITORY, repository_id, "demo")]
    assert world.credentials.get(credential.credential_id) == credential


def test_a_credential_naming_a_retired_or_unknown_secret_or_forge_is_refused(world: _World) -> None:
    with pytest.raises(CredentialSecretUnavailable, match="secret nope is unknown") as caught:
        world.credential(secret="nope")
    assert caught.value.code == "secret_unavailable"
    with pytest.raises(ForgeUnavailable, match="forge frg_nope is unknown"):
        world.authoring.create_credential(_draft(ForgeId("frg_nope")), _CLI)


def test_retiring_a_secret_or_forge_an_enabled_credential_names_is_refused_until_it_is_retired(world: _World) -> None:
    credential = world.credential()
    named = RecordRef(RecordKind.CREDENTIAL, credential.credential_id, "deploy")
    secret = world.authoring._secrets.get("gh")  # pyright: ignore[reportPrivateUsage]
    assert secret is not None
    with pytest.raises(SecretReferenced) as refused_secret:
        world.authoring.retire_secret(secret, _CLI)
    assert refused_secret.value.referrers == [named]
    with pytest.raises(ForgeInUse) as refused_forge:
        world.authoring.retire_forge(world.forge, _CLI)
    assert refused_forge.value.referrers == [named]

    world.authoring.retire_credential(credential, _CLI)
    assert world.authoring.retire_secret(secret, _CLI)
    assert world.authoring.retire_forge(world.forge, _CLI).retired


def test_enabling_a_credential_whose_secret_is_retired_is_refused(world: _World) -> None:
    credential = world.authoring.retire_credential(world.credential(), _CLI)
    secret = world.authoring._secrets.get("gh")  # pyright: ignore[reportPrivateUsage]
    assert secret is not None
    world.authoring.retire_secret(secret, _CLI)
    with pytest.raises(CredentialSecretUnavailable, match="secret gh is retired"):
        world.authoring.enable_credential(credential, _CLI)


# --- Tenant isolation ------------------------------------------------------------------------------------------------


class _Tenant:
    def __init__(self, engine: Engine, tenant: TenantId, authoring: ConfigAuthoring) -> None:
        self.store = CredentialRecordStore(scoped_connections(engine, tenant))
        self.authoring = authoring
        self.authoring.create_secret(SecretName.parse("gh"), "tok", OP)
        self.forge = authoring.create_forge("ghe", None, "github", ForgeFields(_GHE, None), _CLI)
        created = authoring.create_credential(_draft(self.forge.forge_id, slug="deploy-old"), _CLI)
        self.credential = authoring.edit_credential(created, CredentialEdit(slug="deploy"), _CLI)


def test_two_tenants_hold_a_credential_alike_and_each_reads_and_writes_only_its_own(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    tenants = [hub.add_tenant(label) for label in ("alpha", "beta")]
    first, second = (
        _Tenant(hub.engine, t.tenant, hub.hub_services.tenants.open_services(StoreScope(t.tenant)).config_authoring)
        for t in tenants
    )
    assert first.credential.authority == second.credential.authority == "secret/gh"
    assert first.credential.credential_id != second.credential.credential_id

    for reader, other in ((first, second), (second, first)):
        mine, theirs = reader.credential, other.credential.credential_id
        assert reader.store.get(theirs) is None
        assert reader.store.get_many([theirs, mine.credential_id]) == {mine.credential_id: mine}
        assert reader.store.list_all(include_retired=True) == [mine]
        assert reader.store.get_by_slug(CredentialSlug.parse("deploy")) == mine
        assert reader.store.get_by_former_slug(CredentialSlug.parse("deploy-old")) == mine
        assert reader.store.resolve("deploy") == mine
        assert reader.store.resolve(theirs) is None

    stranger = second.credential
    with pytest.raises(ForgeUnavailable):
        first.authoring.edit_credential(stranger, CredentialEdit(name="Stolen"), _CLI)
    with pytest.raises(HubStoreError, match="No row was found"):
        first.authoring.retire_credential(stranger, _CLI)
    retired_stranger = second.authoring.retire_credential(stranger, _CLI)
    with pytest.raises(ForgeUnavailable):
        first.authoring.enable_credential(retired_stranger, _CLI)
    assert second.store.get(stranger.credential_id) == retired_stranger
    with hub.engine.connect() as conn:
        rows = conn.execute(
            hub_scoped(
                sa.select(credentials.c.tenant_id, credentials.c.name, credentials.c.revision).where(
                    credentials.c.name == "deploy-old"
                )
            )
        ).all()
    assert sorted(rows) == sorted([(tenants[0].tenant, "deploy-old", 2), (tenants[1].tenant, "deploy-old", 3)])
