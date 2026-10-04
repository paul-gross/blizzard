"""``ConfigAuthoring`` over the real stores — one change row per committed write,
committed with the record (component tier, migrated sqlite-on-disk)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, Door, FieldChange, RecordKind, RecordRef
from blizzard.hub.domain.config.work_sources import (
    ConfigRevisionConflict,
    WorkSourceEdit,
    WorkSourceFields,
    WorkSourceLocatorTaken,
    WorkSourceNameTaken,
    WorkSourceRecord,
    WorkSourceSecretUnavailable,
)
from blizzard.hub.domain.secrets import SecretName, SecretReferenced
from blizzard.hub.runtime import migration_runner
from blizzard.hub.secrets import hub_key_provider
from blizzard.hub.store.internal.config_change_store import ConfigChangeStore
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.store.internal.work_source_record_store import WorkSourceRecordStore
from blizzard.hub.store.schema import config_changes
from tests.support import OP, config_authoring, hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
_FIELDS = WorkSourceFields(
    provider="github", locator="acme/demo", api_base=None, web_base=None, annotate=False, secret="gh"
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
        self.log = ConfigChangeStore(connections)
        keys = hub_key_provider({}, data_dir=tmp_path / "data")
        self.authoring: ConfigAuthoring = config_authoring(self.engine, keys=keys, clock=FixedClock(_NOW))
        self.authoring.create_secret(SecretName.parse("gh"), "tok-planted", OP)

    def changes(self) -> list:  # type: ignore[type-arg]
        return list(reversed(self.log.page(before=None, limit=100, record_kind=None, record_key=None)))

    def count(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(config_changes)).scalar_one()

    def source(self, name: str = "demo") -> WorkSourceRecord:
        record = self.sources.get(name)
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
    assert world.sources.referrers_of(["gh", "other"]) == {
        "gh": [RecordRef(RecordKind.WORK_SOURCE, "demo")],
        "other": [],
    }

    world.authoring.retire_work_source(record, OP)

    assert world.sources.referrers_of(["gh"]) == {"gh": []}
    assert world.authoring.retire_secret(secret, OP) is True


def test_the_log_pages_newest_first_and_filters_by_record(world: _World) -> None:
    world.authoring.create_work_source("demo", _FIELDS, OP)
    world.authoring.edit_work_source(world.source(), WorkSourceEdit(annotate=True), OP)

    everything = world.log.page(before=None, limit=100, record_kind=None, record_key=None)
    ids = [c.id for c in everything]
    assert ids == sorted(ids, reverse=True)
    assert len(everything) == 3
    older = world.log.page(before=ids[0], limit=1, record_kind=None, record_key=None)
    assert [c.id for c in older] == [ids[1]]
    only = world.log.page(before=None, limit=100, record_kind=RecordKind.WORK_SOURCE, record_key="demo")
    assert [c.op for c in only] == [ChangeOp.EDIT, ChangeOp.CREATE]
