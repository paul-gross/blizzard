"""Store-migration behaviour for both daemon trees (unit tier).

Covers the three guarantees the scaffold owes ``bzh:manual-migrations``: ``init`` is
idempotent, ``migrate`` goes up and down, and a daemon refuses to start on a revision
mismatch, naming its exact migrate command."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import RevisionMismatchError
from blizzard.hub.store import schema as hub_schema
from blizzard.runner.store import schema as runner_schema
from tests.conftest import Daemon
from tests.migration_support_store_migrations import (
    _schema_snapshot,
)

pytestmark = [pytest.mark.unit]

_SCHEMA_METADATA = {"hub": hub_schema.metadata, "runner": runner_schema.metadata}

# Server defaults a migration gives a column that the store schema leaves undeclared, keyed by table; empty while
# the two agree.
_SERVER_DEFAULT_EXEMPTIONS: dict[str, set[str]] = {}

# Constraint names a hub migration gave that src/blizzard/hub/store/schema.py leaves unnamed, keyed by (table,
# migrated name); each value is the revision that rebuilds the table and settles the name.
_CONSTRAINT_NAME_EXEMPTIONS: dict[tuple[str, str], str] = {}

_NAME_EXEMPTIONS = {"hub": _CONSTRAINT_NAME_EXEMPTIONS, "runner": {}}


def _constraint_names(engine: sa.engine.Engine) -> set[tuple[str, str]]:
    """Every named primary key, foreign key, unique constraint and index, as (table, name)."""
    inspector = sa.inspect(engine)
    names: set[tuple[str, str]] = set()
    for table_name in inspector.get_table_names():
        reflected = [
            inspector.get_pk_constraint(table_name).get("name"),
            *(fk.get("name") for fk in inspector.get_foreign_keys(table_name)),
            *(uq.get("name") for uq in inspector.get_unique_constraints(table_name)),
            *(idx.get("name") for idx in inspector.get_indexes(table_name)),
        ]
        names.update((table_name, name) for name in reflected if name is not None)
    return names


def test_init_creates_config_data_dir_and_migrates_to_head(daemon: Daemon, tmp_path: Path) -> None:
    config = daemon.init(tmp_path)

    assert config.config_path.exists()
    assert config.data_dir.is_dir()
    runner = daemon.runtime.migration_runner(config)
    assert runner.current_revision() == runner.script_head()
    assert runner.is_current()


def test_init_is_idempotent(daemon: Daemon, tmp_path: Path) -> None:
    first = daemon.init(tmp_path)
    written = first.config_path.read_text()

    # Re-running reconciles and no-ops: no error, config untouched, still at head.
    second = daemon.init(tmp_path)

    assert second.config_path.read_text() == written
    assert daemon.runtime.migration_runner(second).is_current()


def test_migrate_up_and_down(daemon: Daemon, tmp_path: Path) -> None:
    config = daemon.init(tmp_path)
    runner = daemon.runtime.migration_runner(config)
    head = runner.script_head()
    assert head is not None
    assert runner.current_revision() == head

    daemon.migrate(tmp_path, down="base")
    assert runner.current_revision() is None

    daemon.migrate(tmp_path)
    assert runner.current_revision() == head


def test_daemon_refuses_on_revision_mismatch(daemon: Daemon, tmp_path: Path) -> None:
    config = daemon.init(tmp_path)
    # Roll the store behind the code's head to simulate a version skew.
    daemon.runtime.migration_runner(config).downgrade("base")

    with pytest.raises(RevisionMismatchError) as excinfo:
        daemon.runtime.ensure_current_revision(config)

    message = str(excinfo.value)
    assert daemon.runtime.MIGRATE_COMMAND in message
    assert daemon.name in message


def test_ensure_current_revision_passes_at_head(daemon: Daemon, tmp_path: Path) -> None:
    config = daemon.init(tmp_path)
    # No exception when the store is migrated exactly to head.
    daemon.runtime.ensure_current_revision(config)


def test_migrated_store_matches_declared_schema(daemon: Daemon, tmp_path: Path) -> None:
    """Head equivalence (``bzh:frozen-revisions``) — a fresh ``base -> head`` store is
    schema-identical to ``metadata.create_all()``, under the normalization above. This is
    what keeps ``tests/runner_fakes.py`` and friends valid stand-ins for the real store."""
    config = daemon.init(tmp_path)
    migrated = create_engine_from_url(config.db_url)
    declared = create_engine_from_url(f"sqlite:///{tmp_path / f'{daemon.name}-declared.db'}")
    try:
        _SCHEMA_METADATA[daemon.name].create_all(declared)
        name_exemptions = _NAME_EXEMPTIONS[daemon.name]
        assert _schema_snapshot(
            migrated, exemptions=_SERVER_DEFAULT_EXEMPTIONS, name_exemptions=name_exemptions
        ) == _schema_snapshot(declared, exemptions=_SERVER_DEFAULT_EXEMPTIONS, name_exemptions=name_exemptions)
        assert set(name_exemptions) <= _constraint_names(migrated), "a name exemption matches no constraint"
    finally:
        migrated.dispose()
        declared.dispose()


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (sa.UniqueConstraint("a", "b"), sa.UniqueConstraint("b", "a")),
        (sa.UniqueConstraint("a", "b"), sa.UniqueConstraint("a", "b", name="uq_pair_a_b")),
        (sa.UniqueConstraint("a", "b", name="uq_pair_a_b"), sa.UniqueConstraint("a", "b", name="uq_pair_ab")),
        (sa.PrimaryKeyConstraint("a", "b"), sa.PrimaryKeyConstraint("a", "b", name="pk_pair")),
    ],
    ids=["unique-columns", "unique-unnamed-vs-named", "unique-renamed", "pk-unnamed-vs-named"],
)
def test_schema_snapshot_tells_constraint_shapes_and_names_apart(
    tmp_path: Path, left: sa.Constraint, right: sa.Constraint
) -> None:
    """SQLite reflects a unique constraint through ``get_unique_constraints`` only, never
    ``get_indexes``; the snapshot reads both, and every constraint's name."""
    snapshots = []
    for side, constraint in (("left", left), ("right", right)):
        metadata = sa.MetaData()
        sa.Table("pair", metadata, sa.Column("a", sa.Text, nullable=False), sa.Column("b", sa.Text), constraint)
        engine = create_engine_from_url(f"sqlite:///{tmp_path / f'{side}.db'}")
        try:
            metadata.create_all(engine)
            snapshots.append(_schema_snapshot(engine, exemptions={}, name_exemptions={}))
        finally:
            engine.dispose()
    assert snapshots[0] != snapshots[1]
