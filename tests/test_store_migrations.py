"""Store-migration behaviour for both daemon trees (unit tier).

Covers the three guarantees the scaffold owes ``bzh:manual-migrations``: ``init`` is
idempotent, ``migrate`` goes up and down, and a daemon refuses to start on a revision
mismatch, naming its exact migrate command."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner, RevisionMismatchError
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.store import MIGRATIONS_DIR as HUB_MIGRATIONS_DIR
from blizzard.hub.store import schema as hub_schema
from blizzard.runner.store import schema as runner_schema
from tests.conftest import Daemon

pytestmark = pytest.mark.unit


def test_init_creates_config_data_dir_and_migrates_to_head(daemon: Daemon, tmp_path: Path) -> None:
    config = daemon.runtime.init_environment(tmp_path)

    assert config.config_path.exists()
    assert config.data_dir.is_dir()
    runner = daemon.runtime.migration_runner(config)
    assert runner.current_revision() == runner.script_head()
    assert runner.is_current()


def test_init_is_idempotent(daemon: Daemon, tmp_path: Path) -> None:
    first = daemon.runtime.init_environment(tmp_path)
    written = first.config_path.read_text()

    # Re-running reconciles and no-ops: no error, config untouched, still at head.
    second = daemon.runtime.init_environment(tmp_path)

    assert second.config_path.read_text() == written
    assert daemon.runtime.migration_runner(second).is_current()


def test_migrate_up_and_down(daemon: Daemon, tmp_path: Path) -> None:
    config = daemon.runtime.init_environment(tmp_path)
    runner = daemon.runtime.migration_runner(config)
    head = runner.script_head()
    assert head is not None
    assert runner.current_revision() == head

    daemon.runtime.migrate(tmp_path, down="base")
    assert runner.current_revision() is None

    daemon.runtime.migrate(tmp_path)
    assert runner.current_revision() == head


def test_daemon_refuses_on_revision_mismatch(daemon: Daemon, tmp_path: Path) -> None:
    config = daemon.runtime.init_environment(tmp_path)
    # Roll the store behind the code's head to simulate a version skew.
    daemon.runtime.migration_runner(config).downgrade("base")

    with pytest.raises(RevisionMismatchError) as excinfo:
        daemon.runtime.ensure_current_revision(config)

    message = str(excinfo.value)
    assert daemon.runtime.MIGRATE_COMMAND in message
    assert daemon.name in message


def test_ensure_current_revision_passes_at_head(daemon: Daemon, tmp_path: Path) -> None:
    config = daemon.runtime.init_environment(tmp_path)
    # No exception when the store is migrated exactly to head.
    daemon.runtime.ensure_current_revision(config)


def test_wrapped_takeover_command_column_survives_migration_roundtrip(tmp_path: Path) -> None:
    """Hub-only (``escalations.wrapped_takeover_command`` has no runner counterpart) —
    downgrades to this revision's own parent by id, not ``"-1"``, so a future revision
    landing between them can't silently break this test's premise."""
    config = hub_runtime.init_environment(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)

    def _has_column() -> bool:
        engine = create_engine_from_url(config.db_url)
        try:
            columns = {c["name"] for c in sa.inspect(engine).get_columns("escalations")}
        finally:
            engine.dispose()
        return "wrapped_takeover_command" in columns

    assert _has_column()

    runner.downgrade("20260801_1600_hub_runner_external_usage")
    assert not _has_column()

    runner.upgrade("head")
    assert _has_column()


_SCHEMA_METADATA = {"hub": hub_schema.metadata, "runner": runner_schema.metadata}

# chunks.model carries a migration-only server_default with no schema.py counterpart —
# pre-existing drift this change does not own (bzh:frozen-revisions plan, Decision 4).
_SERVER_DEFAULT_EXEMPTIONS: dict[str, set[str]] = {"chunks": {"model"}}


def _schema_snapshot(engine: sa.engine.Engine, *, exemptions: dict[str, set[str]]) -> dict[str, object]:
    """A comparable schema fingerprint: table set, and per table its column set (name,
    type, nullability, server default), primary key, foreign keys, and indexes — column
    *order* excluded, since ``add_column`` always appends and a migration can't undo that."""
    inspector = sa.inspect(engine)
    snapshot: dict[str, object] = {}
    for table_name in inspector.get_table_names():
        if table_name == "alembic_version":
            continue
        exempt = exemptions.get(table_name, set())
        columns = frozenset(
            (c["name"], str(c["type"]), c["nullable"], None if c["name"] in exempt else c["default"])
            for c in inspector.get_columns(table_name)
        )
        pk = frozenset(inspector.get_pk_constraint(table_name)["constrained_columns"])
        foreign_keys = frozenset(
            (tuple(sorted(fk["constrained_columns"])), fk["referred_table"], tuple(sorted(fk["referred_columns"])))
            for fk in inspector.get_foreign_keys(table_name)
        )
        indexes = frozenset((tuple(idx["column_names"]), idx["unique"]) for idx in inspector.get_indexes(table_name))
        snapshot[table_name] = (columns, pk, foreign_keys, indexes)
    return snapshot


def test_migrated_store_matches_declared_schema(daemon: Daemon, tmp_path: Path) -> None:
    """Head equivalence (``bzh:frozen-revisions``) — a fresh ``base -> head`` store is
    schema-identical to ``metadata.create_all()``, under the normalization above. This is
    what keeps ``tests/runner_fakes.py`` and friends valid stand-ins for the real store."""
    config = daemon.runtime.init_environment(tmp_path)
    migrated = create_engine_from_url(config.db_url)
    declared = create_engine_from_url(f"sqlite:///{tmp_path / f'{daemon.name}-declared.db'}")
    try:
        _SCHEMA_METADATA[daemon.name].create_all(declared)
        assert _schema_snapshot(migrated, exemptions=_SERVER_DEFAULT_EXEMPTIONS) == _schema_snapshot(
            declared, exemptions=_SERVER_DEFAULT_EXEMPTIONS
        )
    finally:
        migrated.dispose()
        declared.dispose()


# The walking skeleton's live-imported tables (20260713_1218_walking_skeleton_facts.py, hub
# tree) that a later revision reshapes — each entry is that revision's own parent (the id to
# downgrade *to*, i.e. "before"), the reshaped table, and the columns that revision adds.
_HISTORICAL_RESHAPES: list[tuple[str, str, tuple[str, ...]]] = [
    ("20260713_1218_hub_walking_skeleton", "escalations", ("takeover_command",)),
    ("20260713_1635_hub_runner_high_water", "graph_nodes", ("produces", "checks")),
    ("20260718_0930_hub_runner_local_pause_reason", "graph_nodes", ("bounce_cap",)),
    ("20260717_2345_hub_chunk_bounces", "graph_nodes", ("run",)),
    ("20260717_2359_hub_command_nodes", "graph_nodes", ("poll_interval_seconds", "poll_timeout_seconds")),
    ("20260720_1000_hub_chunk_intended_migration", "escalations", ("decision_id",)),
    ("20260721_1000_hub_escalation_decision_id", "graph_nodes", ("session_source",)),
    ("20260721_1600_hub_event_log", "artifacts", ("forge",)),
    ("20260722_1200_hub_artifact_forge", "graph_nodes", ("checks_cwd", "checks_timeout")),
    ("20260722_1200_hub_artifact_forge", "graph_choices", ("requires_checks",)),
    ("20260801_1600_hub_runner_external_usage", "escalations", ("wrapped_takeover_command",)),
]


@pytest.mark.parametrize(
    "parent_revision,table,columns",
    _HISTORICAL_RESHAPES,
    ids=[f"{table}.{columns[0]}" for _, table, columns in _HISTORICAL_RESHAPES],
)
def test_walking_skeleton_table_lacks_a_later_revisions_columns(
    parent_revision: str, table: str, columns: tuple[str, ...], tmp_path: Path
) -> None:
    """Historical accuracy (Decision 5) — head equivalence alone can't tell a correct freeze
    from one that wrongly absorbed a later column (the dependent's guard would just skip it
    and land at the same head anyway). A store migrated **fresh, forward-only** to the
    column's own adding revision's *parent* must lack it; migrated on to head, it must have
    it. This must build the store forward from ``base``, never via downgrade-from-head: a
    later revision's own ``downgrade()`` unconditionally drops its column if present, which
    would mask a walking-skeleton table that wrongly created the column from the start."""
    url = f"sqlite:///{tmp_path / 'store.db'}"
    runner = MigrationRunner(script_location=HUB_MIGRATIONS_DIR, url=url)

    def _columns() -> set[str]:
        engine = create_engine_from_url(url)
        try:
            return {c["name"] for c in sa.inspect(engine).get_columns(table)}
        finally:
            engine.dispose()

    runner.upgrade(parent_revision)
    before = _columns()
    for column in columns:
        assert column not in before

    runner.upgrade("head")
    after = _columns()
    for column in columns:
        assert column in after
