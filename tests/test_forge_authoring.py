"""Forges over the real stores: the forge verbs and their refusals, the old address fields resolved to forges, and a
forge edit moving every record that names it (component tier, migrated sqlite-on-disk)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, Door, FieldChange, RecordKind, RecordRef
from blizzard.hub.domain.config.forge_resolution import (
    REPOSITORY_FORGE_RULES,
    WORK_SOURCE_FORGE_RULES,
    ForgeChoice,
    ForgeConflict,
    ForgeRequired,
)
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
from blizzard.hub.domain.config.work_sources import WorkSourceDraft, WorkSourceEdit
from blizzard.hub.secrets import hub_key_provider
from blizzard.hub.store.internal.config_change_store import ConfigChangeStore
from blizzard.hub.store.internal.forge_record_store import ForgeRecordStore
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.store.internal.work_source_record_store import WorkSourceRecordStore
from blizzard.hub.store.schema import repositories, work_sources
from tests.support import OP, config_authoring, hub_store_connections, migrated_hub_at

pytestmark = pytest.mark.component

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

    def columns(self, table: str, key: str) -> tuple[object, ...]:
        """A record's deprecated address columns, as a hub walked back past the forges would read them."""
        scoped = hub_store_connections(self.engine)
        with scoped.read("columns") as conn:
            if table == "repositories":
                query = scoped.select(repositories, repositories.c.forge_id, repositories.c.forge_api_url).where(
                    repositories.c.repository_id == key
                )
            else:
                query = scoped.select(
                    work_sources, work_sources.c.forge_id, work_sources.c.api_base, work_sources.c.web_base
                ).where(work_sources.c.work_source_id == key)
            return tuple(conn.execute(query).one())


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
    with pytest.raises(ForgeUnavailable, match="retired forge ghe"):
        world.authoring.create_repository_naming("demo", None, _REPO, ForgeChoice(api_base=_GHE), _CLI)


def test_an_old_field_resolves_to_the_forge_holding_its_origin_with_a_warning(world: _World) -> None:
    forge = world.ghe()
    authored = world.authoring.create_repository_naming(
        "demo", None, _REPO, ForgeChoice(api_base="https://GHE.acme.internal/api/v3/"), _CLI
    )
    assert authored.record.fields.forge_id == forge.forge_id
    assert [(w.field, w.forge_slug) for w in authored.warnings] == [("forge_api_url", "ghe")]
    assert world.columns("repositories", authored.record.repository_id) == (forge.forge_id, _GHE)


def test_an_old_field_no_forge_holds_makes_one_as_the_migration_slugs_it(world: _World) -> None:
    authored = world.authoring.create_repository_naming(
        "demo", None, _REPO, ForgeChoice(api_base="http://127.0.0.1:8123/"), _CLI
    )
    forge = world.forges.get(authored.record.fields.forge_id)
    assert forge is not None
    assert (forge.slug, forge.name, forge.fields.api_base, forge.created_by) == (
        "127-0-0-1-8123",
        "127.0.0.1:8123",
        "http://127.0.0.1:8123",
        "alice",
    )
    rows = world.log.page(before=None, limit=10, record_kind=RecordKind.FORGE, record_key=forge.forge_id)
    assert [(row.op, row.door) for row in rows] == [(ChangeOp.CREATE, Door.CLI)]


def test_a_work_source_created_with_no_address_is_on_github_with_a_warning(world: _World) -> None:
    authored = world.authoring.create_work_source_naming("demo", None, _SOURCE, ForgeChoice(), _CLI)
    forge = world.forges.get(authored.record.fields.forge_id)
    assert forge is not None and (forge.slug, forge.fields.api_base) == ("github", GITHUB_API_BASE)
    assert [w.field for w in authored.warnings] == ["api_base"]
    assert world.columns("work_sources", authored.record.work_source_id) == (forge.forge_id, GITHUB_API_BASE, None)


def test_an_edit_naming_no_forge_keeps_it_silently_and_a_null_moves_it_to_github(world: _World) -> None:
    forge = world.ghe()
    source = world.authoring.create_work_source_naming("demo", None, _SOURCE, ForgeChoice(api_base=_GHE), _CLI).record
    kept = world.authoring.edit_work_source_naming(source, WorkSourceEdit(), ForgeChoice(), _CLI)
    assert kept.record.fields.forge_id == forge.forge_id and kept.warnings == ()
    moved = world.authoring.edit_work_source_naming(source, WorkSourceEdit(), ForgeChoice(api_base=None), _CLI)
    github = world.forges.get(moved.record.fields.forge_id)
    assert github is not None and github.slug == "github"
    assert [(w.field, w.forge_slug) for w in moved.warnings] == [("api_base", "github")]


def test_an_old_field_disagreeing_with_the_named_forge_is_refused(world: _World) -> None:
    world.ghe()
    with pytest.raises(ForgeConflict):
        world.authoring.create_repository_naming(
            "demo", None, _REPO, ForgeChoice(forge="ghe", api_base=GITHUB_API_BASE), _CLI
        )
    with pytest.raises(ForgeConflict, match="web origin of forge ghe"):
        world.authoring.create_work_source_naming(
            "demo", None, _SOURCE, ForgeChoice(api_base=_GHE, web_base="https://elsewhere.example"), _CLI
        )


def test_a_repository_naming_no_forge_is_refused(world: _World) -> None:
    with pytest.raises(ForgeRequired):
        world.authoring.create_repository_naming("demo", None, _REPO, ForgeChoice(), _CLI)


def test_two_concurrent_resolutions_of_one_new_origin_make_one_forge(world: _World) -> None:
    second = config_authoring(world.engine, keys=world.keys, clock=FixedClock(_NOW))
    barrier = threading.Barrier(2)
    found: list[str] = []

    def resolve(authoring: ConfigAuthoring, spelling: str) -> None:
        barrier.wait()
        resolved = authoring.resolve_forge(ForgeChoice(api_base=spelling), REPOSITORY_FORGE_RULES, _CLI)
        assert resolved is not None
        found.append(resolved.forge.forge_id)

    threads = [
        threading.Thread(target=resolve, args=(world.authoring, "https://new.example/")),
        threading.Thread(target=resolve, args=(second, "https://NEW.example")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(found) == 2 and found[0] == found[1]
    assert [f.fields.api_base for f in world.forges.list_all(include_retired=True)] == ["https://new.example"]


def test_a_forge_edit_rewrites_every_referrers_address_columns(world: _World) -> None:
    forge = world.ghe()
    repo = world.authoring.create_repository("demo", None, _REPO.on(forge.forge_id), _CLI)
    source = world.authoring.create_work_source("demo", None, _SOURCE.on(forge.forge_id), _CLI)
    moved = world.authoring.edit_forge(
        forge, ForgeEdit(api_base="https://ghe.acme.example/api/v3", web_base="https://code.acme.example"), _CLI
    )
    assert world.columns("repositories", repo.repository_id) == (forge.forge_id, moved.fields.api_base)
    assert world.columns("work_sources", source.work_source_id) == (
        forge.forge_id,
        "https://ghe.acme.example/api/v3",
        "https://code.acme.example",
    )


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
    source = world.authoring.create_work_source_naming("demo", None, _SOURCE, ForgeChoice(forge="ghe"), _CLI)
    assert source.record.fields.forge_id == forge.forge_id and source.warnings == ()
    with pytest.raises(ForgeUnavailable):
        world.authoring.resolve_forge(ForgeChoice(forge="nope"), WORK_SOURCE_FORGE_RULES, _CLI, provider="github")
