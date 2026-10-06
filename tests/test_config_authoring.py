"""``ConfigAuthoring`` over the real stores — one change row per committed write,
committed with the record (component tier, migrated sqlite-on-disk)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.config.apply import (
    ApplyEntryRefused,
    ConfigDeclaration,
    RepositoryDeclaration,
    StoredConfig,
    WorkSourceDeclaration,
)
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import (
    ChangeContext,
    ChangeOp,
    Door,
    FieldChange,
    RecordKind,
    RecordRef,
    RecordState,
)
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryCoordinateTaken,
    RepositoryEdit,
    RepositoryFields,
    RepositoryNameTaken,
    RepositorySecretUnavailable,
)
from blizzard.hub.domain.config.secrets import SecretName, SecretReferenced
from blizzard.hub.domain.config.work_sources import (
    ConfigRevisionConflict,
    ConfiguredWorkSource,
    WorkSourceEdit,
    WorkSourceFields,
    WorkSourceLocatorTaken,
    WorkSourceNameTaken,
    WorkSourceSecretUnavailable,
)
from blizzard.hub.runtime import migration_runner
from blizzard.hub.secrets import hub_key_provider
from blizzard.hub.store.internal.config_change_store import ConfigChangeStore
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.store.internal.secret_referrers import SecretReferrersStore
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.store.internal.work_source_record_store import WorkSourceRecordStore
from blizzard.hub.store.schema import config_changes
from tests.support import OP, config_authoring, hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
_FIELDS = WorkSourceFields(
    provider="github", locator="acme/demo", api_base=None, web_base=None, annotate=False, secret="gh"
)
_REPO = RepositoryFields(
    forge_api_url="https://api.github.com", owner="acme", repo="demo", base_branch="master", secret_name="gh"
)
_CLI = ChangeContext(actor="alice", door=Door.CLI)


class _World:
    def __init__(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path / 'hub.db'}"
        migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
        self.engine: Engine = create_engine_from_url(db_url)
        connections = hub_store_connections(self.engine)
        self.secrets = SecretStore(connections)
        self.sources = WorkSourceRecordStore(connections)
        self.repos = RepositoryRecordStore(connections)
        self.referrers = SecretReferrersStore(connections)
        self.log = ConfigChangeStore(connections)
        keys = hub_key_provider({}, data_dir=tmp_path / "data")
        self.authoring: ConfigAuthoring = config_authoring(self.engine, keys=keys, clock=FixedClock(_NOW))
        self.authoring.create_secret(SecretName.parse("gh"), "tok-planted", OP)

    def changes(self) -> list:  # type: ignore[type-arg]
        return list(reversed(self.log.page(before=None, limit=100, record_kind=None, record_key=None)))

    def count(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(config_changes)).scalar_one()

    def source(self, name: str = "demo") -> ConfiguredWorkSource:
        record = self.sources.get(name)
        assert record is not None
        return record

    def repo(self, name: str = "demo") -> ConfiguredRepository:
        record = self.repos.get(name)
        assert record is not None
        return record


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def test_a_secret_create_appends_one_row_with_no_value(world: _World) -> None:
    (row,) = world.changes()
    assert (row.record_kind, row.record_key, row.revision, row.op, row.actor, row.door) == (
        RecordKind.SECRET,
        "gh",
        1,
        ChangeOp.CREATE,
        "op",
        Door.API,
    )
    assert row.diff == ()
    assert "tok-planted" not in repr(row)


def test_each_secret_verb_appends_exactly_one_row(world: _World) -> None:
    record = world.secrets.get("gh")
    assert record is not None
    replaced = world.authoring.replace_secret(record, "v2", _CLI)
    world.authoring.retire_secret(replaced, _CLI)
    world.authoring.enable_secret(replaced, _CLI)

    ops = [(c.op, c.revision, c.door) for c in world.changes()[1:]]
    assert ops == [
        (ChangeOp.REPLACE, 2, Door.CLI),
        (ChangeOp.RETIRE, 2, Door.CLI),
        (ChangeOp.ENABLE, 2, Door.CLI),
    ]
    assert world.changes()[2].diff == (FieldChange("retired", False, True),)


def test_a_secret_retire_or_enable_that_changes_nothing_appends_nothing(world: _World) -> None:
    record = world.secrets.get("gh")
    assert record is not None
    assert world.authoring.enable_secret(record, OP) is False
    assert world.authoring.retire_secret(record, OP) is True
    assert world.authoring.retire_secret(record, OP) is False
    assert world.count() == 2


def test_a_work_source_create_records_the_diff_and_revision_one(world: _World) -> None:
    created = world.authoring.create_work_source("demo", _FIELDS, _CLI)

    assert (created.revision, created.created_by, created.retired) == (1, "alice", False)
    row = world.changes()[-1]
    assert (row.record_kind, row.record_key, row.op, row.revision, row.door) == (
        RecordKind.WORK_SOURCE,
        "demo",
        ChangeOp.CREATE,
        1,
        Door.CLI,
    )
    assert {d.field: (d.old, d.new) for d in row.diff} == {
        "provider": (None, "github"),
        "locator": (None, "acme/demo"),
        "annotate": (None, False),
        "secret": (None, "gh"),
    }


def test_edit_retire_and_enable_each_move_the_revision_and_append_one_row(world: _World) -> None:
    record = world.authoring.create_work_source("demo", _FIELDS, OP)
    edited = world.authoring.edit_work_source(record, WorkSourceEdit(annotate=True), _CLI)
    retired = world.authoring.retire_work_source(edited, _CLI)
    enabled = world.authoring.enable_work_source(retired, _CLI)

    assert [r.revision for r in (edited, retired, enabled)] == [2, 3, 4]
    assert world.source() == enabled
    tail = [(c.op, c.revision) for c in world.changes()[-3:]]
    assert tail == [(ChangeOp.EDIT, 2), (ChangeOp.RETIRE, 3), (ChangeOp.ENABLE, 4)]
    assert world.changes()[-3].diff == (FieldChange("annotate", False, True),)
    assert world.changes()[-2].diff == (FieldChange("retired", False, True),)
    assert world.sources.list_all(include_retired=False) == [enabled]


def test_a_write_that_changes_nothing_writes_nothing(world: _World) -> None:
    record = world.authoring.create_work_source("demo", _FIELDS, OP)
    before = world.count()

    assert world.authoring.edit_work_source(record, WorkSourceEdit(), OP) == record
    assert world.authoring.edit_work_source(record, WorkSourceEdit(annotate=False, secret="gh"), OP) == record
    assert world.authoring.enable_work_source(record, OP) == record
    retired = world.authoring.retire_work_source(record, OP)
    assert world.authoring.retire_work_source(retired, OP) == retired

    assert world.count() == before + 1
    assert world.source().revision == 2


def test_a_stale_revision_conflicts_and_writes_nothing(world: _World) -> None:
    record = world.authoring.create_work_source("demo", _FIELDS, OP)
    world.authoring.edit_work_source(record, WorkSourceEdit(annotate=True), OP)
    before = world.count()

    with pytest.raises(ConfigRevisionConflict) as caught:
        world.authoring.edit_work_source(record, WorkSourceEdit(annotate=False, web_base="https://x"), OP)
    assert caught.value.current == 2
    with pytest.raises(ConfigRevisionConflict):
        world.authoring.retire_work_source(record, OP, if_match=1)
    assert world.count() == before


def test_a_cleared_field_reads_back_null(world: _World) -> None:
    fields = WorkSourceFields("github", "acme/demo", "https://api.x", "https://web.x", False, "gh")
    record = world.authoring.create_work_source("demo", fields, OP)
    world.authoring.edit_work_source(record, WorkSourceEdit(api_base=None), OP)
    assert world.source().fields.api_base is None
    assert world.source().fields.web_base == "https://web.x"


def test_a_taken_name_or_locator_is_refused_and_names_the_holder(world: _World) -> None:
    world.authoring.create_work_source("demo", _FIELDS, OP)
    before = world.count()

    with pytest.raises(WorkSourceNameTaken):
        world.authoring.create_work_source("demo", _FIELDS, OP)
    with pytest.raises(WorkSourceLocatorTaken) as caught:
        world.authoring.create_work_source("demo2", _FIELDS, OP)
    assert caught.value.holder == "demo"
    assert world.count() == before


def test_a_retired_source_keeps_its_locator_claim(world: _World) -> None:
    record = world.authoring.create_work_source("demo", _FIELDS, OP)
    world.authoring.retire_work_source(record, OP)

    with pytest.raises(WorkSourceLocatorTaken) as caught:
        world.authoring.create_work_source("demo2", _FIELDS, OP)
    assert caught.value.holder == "demo"


def test_a_missing_or_retired_secret_is_refused_and_nothing_is_written(world: _World) -> None:
    before = world.count()
    with pytest.raises(WorkSourceSecretUnavailable, match="unknown"):
        world.authoring.create_work_source("demo", WorkSourceFields("github", "a/b", None, None, False, "nope"), OP)
    secret = world.secrets.get("gh")
    assert secret is not None
    world.authoring.retire_secret(secret, OP)
    with pytest.raises(WorkSourceSecretUnavailable, match="retired"):
        world.authoring.create_work_source("demo", _FIELDS, OP)
    assert world.count() == before + 1
    assert world.sources.get("demo") is None


def test_enabling_a_source_whose_secret_was_retired_is_refused(world: _World) -> None:
    record = world.authoring.create_work_source("demo", _FIELDS, OP)
    retired = world.authoring.retire_work_source(record, OP)
    secret = world.secrets.get("gh")
    assert secret is not None
    world.authoring.retire_secret(secret, OP)
    before = world.count()

    with pytest.raises(WorkSourceSecretUnavailable):
        world.authoring.enable_work_source(retired, OP)
    assert world.count() == before
    assert world.source().retired is True


def test_retiring_a_referenced_secret_is_refused_and_nothing_is_written(world: _World) -> None:
    world.authoring.create_work_source("demo", _FIELDS, OP)
    secret = world.secrets.get("gh")
    assert secret is not None
    before = world.count()

    with pytest.raises(SecretReferenced) as caught:
        world.authoring.retire_secret(secret, OP)
    assert caught.value.referrers == [RecordRef(RecordKind.WORK_SOURCE, "demo")]
    assert "work_source demo" in str(caught.value)
    assert not world.secrets.is_retired("gh")
    assert world.count() == before


def test_a_retired_referrer_no_longer_blocks_the_secret(world: _World) -> None:
    record = world.authoring.create_work_source("demo", _FIELDS, OP)
    secret = world.secrets.get("gh")
    assert secret is not None
    assert world.referrers.referrers_of(["gh", "other"]) == {
        "gh": [RecordRef(RecordKind.WORK_SOURCE, "demo")],
        "other": [],
    }

    world.authoring.retire_work_source(record, OP)

    assert world.referrers.referrers_of(["gh"]) == {"gh": []}
    assert world.authoring.retire_secret(secret, OP) is True


def test_the_log_pages_newest_first_and_filters_by_record(world: _World) -> None:
    world.authoring.create_work_source("demo", _FIELDS, OP)
    world.authoring.edit_work_source(world.source(), WorkSourceEdit(annotate=True), OP)

    everything = world.log.page(before=None, limit=100, record_kind=None, record_key=None)
    ids = [c.id for c in everything if c.id is not None]
    assert ids == sorted(ids, reverse=True)
    assert len(everything) == 3
    older = world.log.page(before=ids[0], limit=1, record_kind=None, record_key=None)
    assert [c.id for c in older] == [ids[1]]
    only = world.log.page(before=None, limit=100, record_kind=RecordKind.WORK_SOURCE, record_key="demo")
    assert [c.op for c in only] == [ChangeOp.EDIT, ChangeOp.CREATE]


# --- Repositories ------------------------------------------------------------------


def test_a_repository_create_records_the_diff_and_revision_one(world: _World) -> None:
    created = world.authoring.create_repository("demo", _REPO, _CLI)

    assert (created.revision, created.created_by, created.retired) == (1, "alice", False)
    assert world.repo() == created
    row = world.changes()[-1]
    assert (row.record_kind, row.record_key, row.op, row.revision, row.door) == (
        RecordKind.REPOSITORY,
        "demo",
        ChangeOp.CREATE,
        1,
        Door.CLI,
    )
    assert {d.field: (d.old, d.new) for d in row.diff} == {
        "forge_api_url": (None, "https://api.github.com"),
        "owner": (None, "acme"),
        "repo": (None, "demo"),
        "base_branch": (None, "master"),
        "secret_name": (None, "gh"),
    }


def test_repository_edit_retire_and_enable_each_move_the_revision_and_append_one_row(world: _World) -> None:
    record = world.authoring.create_repository("demo", _REPO, OP)
    edited = world.authoring.edit_repository(record, RepositoryEdit(base_branch="main"), _CLI, if_match=1)
    retired = world.authoring.retire_repository(edited, _CLI)
    enabled = world.authoring.enable_repository(retired, _CLI)

    assert [r.revision for r in (edited, retired, enabled)] == [2, 3, 4]
    assert world.repo() == enabled
    tail = [(c.op, c.revision, c.record_kind) for c in world.changes()[-3:]]
    assert tail == [
        (ChangeOp.EDIT, 2, RecordKind.REPOSITORY),
        (ChangeOp.RETIRE, 3, RecordKind.REPOSITORY),
        (ChangeOp.ENABLE, 4, RecordKind.REPOSITORY),
    ]
    assert world.changes()[-3].diff == (FieldChange("base_branch", "master", "main"),)
    assert world.repos.list_all(include_retired=False) == [enabled]


def test_a_retired_repository_reads_the_time_of_its_newest_retirement_and_an_enabled_one_none(world: _World) -> None:
    record = world.authoring.create_repository("demo", _REPO, OP)
    assert world.repo().retired_at is None

    retired = world.authoring.retire_repository(record, OP)
    assert (world.repo().retired, world.repo().retired_at) == (True, _NOW)
    assert world.repos.list_all(include_retired=True)[0].retired_at == _NOW

    world.authoring.enable_repository(retired, OP)
    assert (world.repo().retired, world.repo().retired_at) == (False, None)


def test_a_repository_write_that_changes_nothing_writes_nothing(world: _World) -> None:
    record = world.authoring.create_repository("demo", _REPO, OP)
    before = world.count()

    assert world.authoring.edit_repository(record, RepositoryEdit(owner="acme"), OP) == record
    assert world.authoring.enable_repository(record, OP) == record
    assert world.count() == before


def test_a_stale_repository_revision_conflicts_and_writes_nothing(world: _World) -> None:
    record = world.authoring.create_repository("demo", _REPO, OP)
    world.authoring.edit_repository(record, RepositoryEdit(base_branch="main"), OP)
    before = world.count()

    with pytest.raises(ConfigRevisionConflict) as caught:
        world.authoring.edit_repository(record, RepositoryEdit(base_branch="dev"), OP)
    assert caught.value.current == 2
    assert "repository demo" in str(caught.value)
    with pytest.raises(ConfigRevisionConflict):
        world.authoring.retire_repository(world.repo(), OP, if_match=1)
    assert world.count() == before


def test_a_taken_repository_name_or_coordinate_is_refused_naming_the_holder(world: _World) -> None:
    record = world.authoring.create_repository("demo", _REPO, OP)
    world.authoring.retire_repository(record, OP)
    before = world.count()

    with pytest.raises(RepositoryNameTaken):
        world.authoring.create_repository("demo", _REPO, OP)
    with pytest.raises(RepositoryCoordinateTaken) as caught:
        world.authoring.create_repository("demo2", _REPO, OP)
    assert caught.value.holder == "demo"
    other = world.authoring.create_repository("other", RepositoryFields(**{**_REPO.__dict__, "repo": "other"}), OP)
    with pytest.raises(RepositoryCoordinateTaken):
        world.authoring.edit_repository(other, RepositoryEdit(repo="demo"), OP)
    assert world.count() == before + 1


def test_a_repository_with_a_missing_or_retired_secret_is_refused(world: _World) -> None:
    before = world.count()
    with pytest.raises(RepositorySecretUnavailable, match="unknown") as caught:
        world.authoring.create_repository("demo", RepositoryFields(**{**_REPO.__dict__, "secret_name": "nope"}), OP)
    assert caught.value.field == "secret_name"
    record = world.authoring.create_repository("demo", _REPO, OP)
    retired = world.authoring.retire_repository(record, OP)
    secret = world.secrets.get("gh")
    assert secret is not None
    world.authoring.retire_secret(secret, OP)
    after = world.count()

    with pytest.raises(RepositorySecretUnavailable, match="retired"):
        world.authoring.enable_repository(retired, OP)
    assert world.count() == after == before + 3
    assert world.repo().retired is True


def test_an_active_repository_blocks_retiring_its_secret_until_it_is_retired(world: _World) -> None:
    source = world.authoring.create_work_source("demo", _FIELDS, OP)
    world.authoring.retire_work_source(source, OP)
    record = world.authoring.create_repository("demo", _REPO, OP)
    secret = world.secrets.get("gh")
    assert secret is not None

    assert world.referrers.referrers_of(["gh"]) == {"gh": [RecordRef(RecordKind.REPOSITORY, "demo")]}
    with pytest.raises(SecretReferenced) as caught:
        world.authoring.retire_secret(secret, OP)
    assert caught.value.referrers == [RecordRef(RecordKind.REPOSITORY, "demo")]

    world.authoring.retire_repository(record, OP)
    assert world.referrers.referrers_of(["gh"]) == {"gh": []}
    assert world.authoring.retire_secret(secret, OP) is True


def test_referrers_union_every_kind_ordered_by_kind_then_key(world: _World) -> None:
    world.authoring.create_work_source("demo", _FIELDS, OP)
    world.authoring.create_repository("demo", _REPO, OP)

    assert world.referrers.referrers_of(["gh"]) == {
        "gh": [RecordRef(RecordKind.REPOSITORY, "demo"), RecordRef(RecordKind.WORK_SOURCE, "demo")]
    }


# --- Declarative apply -----------------------------------------------------------------

_APPLY = ChangeContext(actor="alice", door=Door.APPLY, apply_id="apl_ONE")


def _source_declaration(name: str, locator: str, **stated: object) -> WorkSourceDeclaration:
    return WorkSourceDeclaration(
        name=name,
        fields=replace(_FIELDS, locator=locator),
        edit=WorkSourceEdit(**stated),  # type: ignore[arg-type]
    )


def _repo_declaration(name: str, **stated: object) -> RepositoryDeclaration:
    return RepositoryDeclaration(name=name, fields=_REPO, edit=RepositoryEdit(**stated))  # type: ignore[arg-type]


def _apply(world: _World, declaration: ConfigDeclaration, ctx: ChangeContext = _APPLY, *, dry_run: bool = False):  # type: ignore[no-untyped-def]
    stored = StoredConfig(
        work_sources=world.sources.get_many([d.name for d in declaration.work_sources]),
        repositories=world.repos.get_many([d.name for d in declaration.repositories]),
        secrets=dict.fromkeys(declaration.secrets, RecordState.ACTIVE),
    )
    return world.authoring.apply(declaration, stored, ctx, dry_run=dry_run)


_DECLARATION = ConfigDeclaration(
    secrets=("gh",),
    work_sources=(_source_declaration("demo", "acme/demo"),),
    repositories=(_repo_declaration("demo"),),
)


def test_an_apply_commits_every_write_with_the_door_and_one_shared_apply_id(world: _World) -> None:
    before = world.count()
    outcomes = _apply(world, _DECLARATION)
    assert [(o.kind, o.key, o.op) for o in outcomes] == [
        (RecordKind.WORK_SOURCE, "demo", ChangeOp.CREATE),
        (RecordKind.REPOSITORY, "demo", ChangeOp.CREATE),
    ]
    rows = world.changes()[before:]
    assert [(r.door, r.apply_id, r.actor) for r in rows] == [(Door.APPLY, "apl_ONE", "alice")] * 2
    assert world.source().revision == 1 and world.repo().revision == 1


def test_a_second_apply_of_the_same_declaration_appends_nothing(world: _World) -> None:
    _apply(world, _DECLARATION)
    written = world.count()
    declaration = replace(
        _DECLARATION,
        work_sources=(
            replace(_source_declaration("demo", "acme/demo"), edit=WorkSourceEdit(locator="acme/demo", annotate=False)),
        ),
    )
    outcomes = _apply(world, declaration)
    assert [o.op for o in outcomes] == [None, None]
    assert world.count() == written


def test_a_dry_run_writes_nothing_and_returns_the_outcomes_of_the_real_apply(world: _World) -> None:
    before = world.count()
    dry = _apply(world, _DECLARATION, replace(_APPLY, apply_id=None), dry_run=True)
    assert world.count() == before
    assert world.sources.get("demo") is None and world.repos.get("demo") is None
    assert _apply(world, _DECLARATION) == dry


def test_a_dry_run_reaches_the_refusals_a_real_apply_reaches(world: _World) -> None:
    world.authoring.retire_secret(world.secrets.get("gh"), OP)  # type: ignore[arg-type]
    before = world.count()
    for dry_run in (True, False):
        with pytest.raises(ApplyEntryRefused) as caught:
            _apply(world, replace(_DECLARATION, secrets=()), dry_run=dry_run)
        assert isinstance(caught.value.cause, WorkSourceSecretUnavailable)
    assert world.count() == before


def test_a_refusal_on_a_later_entry_leaves_the_earlier_creates_unwritten(world: _World) -> None:
    before = world.count()
    declaration = ConfigDeclaration(
        work_sources=(
            _source_declaration("first", "acme/first"),
            _source_declaration("second", "acme/second"),
            _source_declaration("third", "acme/first"),
        )
    )
    with pytest.raises(ApplyEntryRefused) as caught:
        _apply(world, declaration)
    assert (caught.value.section, caught.value.index) == ("work_sources", 2)
    assert isinstance(caught.value.cause, WorkSourceLocatorTaken)
    assert world.count() == before
    assert world.sources.get("first") is None and world.sources.get("second") is None


def test_an_edit_and_an_enable_commit_in_one_transaction_at_consecutive_revisions(world: _World) -> None:
    _apply(world, _DECLARATION)
    world.authoring.retire_work_source(world.source(), OP)
    outcomes = _apply(
        world, replace(_DECLARATION, work_sources=(_source_declaration("demo", "acme/demo", annotate=True),))
    )
    assert [o.op for o in outcomes] == [ChangeOp.ENABLE, ChangeOp.EDIT, None]
    record = world.source()
    assert (record.revision, record.retired, record.fields.annotate) == (4, False, True)


def test_a_lost_race_on_a_row_refuses_the_whole_apply(world: _World) -> None:
    _apply(world, _DECLARATION)
    stale = world.source()
    world.authoring.edit_work_source(stale, WorkSourceEdit(annotate=True), OP)
    declaration = ConfigDeclaration(work_sources=(_source_declaration("demo", "acme/demo", annotate=True),))
    stored = StoredConfig(work_sources={"demo": stale}, repositories={}, secrets={})
    before = world.count()
    with pytest.raises(ApplyEntryRefused) as caught:
        world.authoring.apply(declaration, stored, _APPLY, dry_run=False)
    assert isinstance(caught.value.cause, ConfigRevisionConflict)
    assert world.count() == before
