"""Forges over the real stores: the forge verbs and their refusals, a record write naming its forge — and refused an
address field the forge carries — and a forge edit moving every record that names it (component tier, migrated
sqlite-on-disk)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, Door, FieldChange, RecordKind, RecordRef
from blizzard.hub.domain.config.forge_resolution import FieldMovedToForge, ForgeChoice, ForgeRequired, MovedField
from blizzard.hub.domain.config.forges import (
    GITHUB_API_BASE,
    ConfiguredForge,
    ForgeEdit,
    ForgeFields,
    ForgeInUse,
    ForgeOriginTaken,
    ForgeRetired,
    ForgeUnavailable,
)
from blizzard.hub.domain.config.repositories import RepositoryDraft, RepositoryEdit
from blizzard.hub.domain.config.secrets import SecretName
from blizzard.hub.domain.config.work_sources import (
    WorkSourceDraft,
    WorkSourceEdit,
)
from blizzard.hub.secrets import hub_key_provider
from blizzard.hub.store.internal.config_change_store import ConfigChangeStore
from blizzard.hub.store.internal.forge_record_store import ForgeRecordStore
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.store.internal.work_source_record_store import WorkSourceRecordStore
from tests.support.scaffolding import OP, config_authoring, hub_store_connections, migrated_hub_at

_NOW = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)
_CLI = ChangeContext(actor="alice", door=Door.CLI)
_GHE = "https://ghe.acme.internal/api/v3"
_REPO = RepositoryDraft(owner="acme", repo="demo", base_branch="main", secret_name="gh")
_SOURCE = WorkSourceDraft(provider="github", locator="acme/demo", annotate=False, secret="gh")


class _World:
    def __init__(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path / 'hub.db'}"
        migrated_hub_at(db_url)
        self.engine: Engine = create_engine_from_url(db_url)
        connections = hub_store_connections(self.engine)
        self.forges = ForgeRecordStore(connections)
        self.repos = RepositoryRecordStore(connections)
        self.sources = WorkSourceRecordStore(connections)
        self.log = ConfigChangeStore(connections)
        self.keys = hub_key_provider({}, data_dir=tmp_path / "data")
        self.authoring: ConfigAuthoring = config_authoring(self.engine, keys=self.keys, clock=FixedClock(_NOW))
        self.authoring.create_secret(SecretName.parse("gh"), "tok", OP)

    def ghe(self) -> ConfiguredForge:
        return self.authoring.create_forge("ghe", "Acme GHE", "github", ForgeFields(_GHE, None), _CLI)


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def test_a_forge_is_stored_canonical_and_its_create_logged_by_id(world: _World) -> None:
    forge = world.authoring.create_forge(
        "ghe", None, "github", ForgeFields("HTTPS://GHE.Acme.Internal:443/api/v3/", " "), _CLI
    )
    assert forge.fields == ForgeFields(_GHE, None)
    assert world.forges.resolve("ghe") == forge
    (row,) = world.log.page(before=None, limit=10, record_kind=RecordKind.FORGE, record_key=None)
    assert (row.record_key, row.op, row.diff) == (
        forge.forge_id,
        ChangeOp.CREATE,
        (
            FieldChange("slug", None, "ghe"),
            FieldChange("name", None, "ghe"),
            FieldChange("kind", None, "github"),
            FieldChange("api_base", None, _GHE),
        ),
    )


def test_an_origin_another_forge_holds_is_refused_naming_it(world: _World) -> None:
    world.ghe()
    with pytest.raises(ForgeOriginTaken, match="forge ghe"):
        world.authoring.create_forge("other", None, "github", ForgeFields(f"{_GHE}/", None), _CLI)
    other = world.authoring.create_forge("other", None, "github", ForgeFields("https://ghe2.acme.internal", None), _CLI)
    with pytest.raises(ForgeOriginTaken, match="forge ghe"):
        world.authoring.edit_forge(other, ForgeEdit(api_base=_GHE), _CLI)


def test_a_forge_an_enabled_record_names_is_refused_retirement_naming_it(world: _World) -> None:
    forge = world.ghe()
    repo = world.authoring.create_repository("demo", None, _REPO.on(forge.forge_id), _CLI)
    with pytest.raises(ForgeInUse) as caught:
        world.authoring.retire_forge(forge, _CLI)
    assert caught.value.referrers == [RecordRef(RecordKind.REPOSITORY, repo.repository_id, "demo")]
    world.authoring.retire_repository(repo, _CLI)
    retired = world.authoring.retire_forge(forge, _CLI)
    assert world.forges.get(forge.forge_id) == retired and retired.retired


def test_enabling_a_record_whose_forge_is_retired_is_refused_naming_it(world: _World) -> None:
    forge = world.ghe()
    repo = world.authoring.create_repository("demo", None, _REPO.on(forge.forge_id), _CLI)
    repo = world.authoring.retire_repository(repo, _CLI)
    forge = world.authoring.retire_forge(forge, _CLI)
    with pytest.raises(ForgeRetired, match="forge ghe"):
        world.authoring.enable_repository(repo, _CLI)
    world.authoring.enable_forge(forge, _CLI)
    assert not world.authoring.enable_repository(repo, _CLI).retired


def test_a_write_naming_a_retired_forge_is_refused(world: _World) -> None:
    forge = world.authoring.retire_forge(world.ghe(), _CLI)
    with pytest.raises(ForgeUnavailable):
        world.authoring.create_repository("demo", None, _REPO.on(forge.forge_id), _CLI)
    with pytest.raises(ForgeUnavailable, match="forge ghe is retired"):
        world.authoring.create_repository_naming("demo", None, _REPO, ForgeChoice(forge="ghe", named=forge), _CLI)


def test_a_record_created_naming_no_forge_is_refused(world: _World) -> None:
    world.ghe()
    with pytest.raises(ForgeRequired):
        world.authoring.create_repository_naming("demo", None, _REPO, ForgeChoice(), _CLI)
    with pytest.raises(ForgeRequired):
        world.authoring.create_work_source_naming("demo", None, _SOURCE, ForgeChoice(), _CLI)
    assert world.repos.list_all(include_retired=True) == []
    assert world.sources.list_all(include_retired=True) == []


def test_an_address_field_is_refused_naming_the_forge_holding_its_origin_and_writes_nothing(world: _World) -> None:
    world.ghe()
    choice = ForgeChoice(moved=(MovedField("forge_api_url", "https://GHE.acme.internal/api/v3/"),))
    with pytest.raises(FieldMovedToForge, match="forge ghe"):
        world.authoring.create_repository_naming("demo", None, _REPO, choice, _CLI)
    assert world.repos.list_all(include_retired=True) == []
    assert [forge.slug for forge in world.forges.list_all(include_retired=True)] == ["ghe"]


def test_an_address_field_on_an_edit_is_refused_naming_the_records_forge(world: _World) -> None:
    forge = world.ghe()
    source = world.authoring.create_work_source_naming(
        "demo", None, _SOURCE, ForgeChoice(forge="ghe", named=forge), _CLI
    )
    choice = ForgeChoice(moved=(MovedField("api_base", GITHUB_API_BASE),))
    with pytest.raises(FieldMovedToForge, match="forge ghe"):
        world.authoring.edit_work_source_naming(source, WorkSourceEdit(annotate=True), choice, _CLI)
    assert world.sources.get(source.work_source_id) == source


def test_a_repository_edit_names_its_forge_by_id_in_the_change_log(world: _World) -> None:
    ghe, github = (
        world.ghe(),
        world.authoring.create_forge("github", "GitHub", "github", ForgeFields(GITHUB_API_BASE, None), _CLI),
    )
    repo = world.authoring.create_repository("demo", None, _REPO.on(ghe.forge_id), _CLI)
    world.authoring.edit_repository(repo, RepositoryEdit(forge_id=github.forge_id), _CLI)
    (row, _) = world.log.page(before=None, limit=2, record_kind=RecordKind.REPOSITORY, record_key=repo.repository_id)
    assert row.diff == (FieldChange("forge", ghe.forge_id, github.forge_id),)


def test_a_work_source_naming_its_forge_needs_one_of_its_providers_kind(world: _World) -> None:
    forge = world.ghe()
    source = world.authoring.create_work_source_naming(
        "demo", None, _SOURCE, ForgeChoice(forge="ghe", named=forge), _CLI
    )
    assert source.fields.forge_id == forge.forge_id
    with pytest.raises(ForgeUnavailable, match="forge nope is unknown"):
        world.authoring.create_work_source_naming("other", None, _SOURCE, ForgeChoice(forge="nope"), _CLI)
