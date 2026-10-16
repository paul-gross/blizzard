"""Helpers the ``store_migrations`` tests share across the revisions they prove."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.store import MIGRATIONS_DIR as HUB_MIGRATIONS_DIR
from blizzard.runner.store import MIGRATIONS_DIR as RUNNER_MIGRATIONS_DIR
from tests.support import MIGRATION_CLOCK, upgrade_runner_store_as_registered


def _compared_name(table_name: str, name: str | None, name_exemptions: dict[tuple[str, str], str]) -> str | None:
    """A constraint's name as the snapshot compares it — ``None`` for an unnamed or an exempted one."""
    return None if name is None or (table_name, name) in name_exemptions else name


def _schema_snapshot(
    engine: sa.engine.Engine,
    *,
    exemptions: dict[str, set[str]],
    name_exemptions: dict[tuple[str, str], str],
) -> dict[str, object]:
    """A comparable schema fingerprint: table set, and per table its column set (name,
    type, nullability, server default), primary key, foreign keys, unique constraints and
    indexes — each constraint with its name, ``None`` where it is unnamed. Column *order*
    is excluded, since ``add_column`` always appends and a migration can't undo that."""
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
        pk_constraint = inspector.get_pk_constraint(table_name)
        pk = (
            _compared_name(table_name, pk_constraint.get("name"), name_exemptions),
            frozenset(pk_constraint["constrained_columns"]),
        )
        foreign_keys = frozenset(
            (
                _compared_name(table_name, fk.get("name"), name_exemptions),
                tuple(sorted(fk["constrained_columns"])),
                fk["referred_table"],
                tuple(sorted(fk["referred_columns"])),
            )
            for fk in inspector.get_foreign_keys(table_name)
        )
        uniques = frozenset(
            (_compared_name(table_name, uq.get("name"), name_exemptions), tuple(uq["column_names"]))
            for uq in inspector.get_unique_constraints(table_name)
        )
        indexes = frozenset(
            (idx["name"], tuple(idx["column_names"]), idx["unique"]) for idx in inspector.get_indexes(table_name)
        )
        snapshot[table_name] = (columns, pk, foreign_keys, uniques, indexes)
    return snapshot


_MIGRATIONS_DIRS = {"hub": HUB_MIGRATIONS_DIR, "runner": RUNNER_MIGRATIONS_DIR}


def assert_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    """Historical accuracy (``bzh:frozen-revisions``) — head equivalence alone can't tell a
    correct freeze from one that wrongly absorbed a later reshape, so a store migrated to the
    reshaping revision's parent must show the pre-reshape shape and head the post-reshape one.
    Each ``(tree, parent, table, columns, direction)`` case is parametrized by the colocated test of the revision
    that reshapes the table, ``direction`` being ``"added"`` or ``"removed"``."""
    url = f"sqlite:///{tmp_path / 'store.db'}"
    runner = MigrationRunner(script_location=_MIGRATIONS_DIRS[tree], url=url, clock=MIGRATION_CLOCK)

    def _columns() -> set[str]:
        engine = create_engine_from_url(url)
        try:
            return {c["name"] for c in sa.inspect(engine).get_columns(table)}
        finally:
            engine.dispose()

    # Forward from ``base``, never downgrade-from-head: the reshaping revision's own
    # ``downgrade()`` drops the column if present, masking a freeze that wrongly created it.
    runner.upgrade(parent_revision)
    before = _columns()

    runner.upgrade("head")
    after = _columns()

    if direction == "added":
        for column in columns:
            assert column not in before
            assert column in after
    else:
        for column in columns:
            assert column in before
            assert column not in after


def _spawn_cwd_round_trip(  # type: ignore[no-untyped-def]
    config, runner, parent: str, row: dict[str, object], *, registered: bool = False
) -> list[object]:
    """Downgrade to ``parent``, seed one segment row there, upgrade — the column's value on that row. A runner store
    upgrades with a runner registered (``registered``)."""
    runner.downgrade(parent)
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.begin() as conn:
            assert "spawn_cwd" not in {c["name"] for c in sa.inspect(conn).get_columns("transcript_segments")}
            columns = ", ".join(row)
            params = ", ".join(f":{k}" for k in row)
            conn.execute(sa.text(f"INSERT INTO transcript_segments ({columns}) VALUES ({params})"), row)
    finally:
        engine.dispose()
    if registered:
        upgrade_runner_store_as_registered(runner, config.db_url)
    else:
        runner.upgrade("head")
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.connect() as conn:
            return [r.spawn_cwd for r in conn.execute(sa.text("SELECT spawn_cwd FROM transcript_segments"))]
    finally:
        engine.dispose()
