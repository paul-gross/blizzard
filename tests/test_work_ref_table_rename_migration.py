"""``chunk_pm_pointers`` -> ``chunk_work_refs`` — the issue-#55 store rename.

A pure ``op.rename_table``: the assertion that matters is that rows survive, so these
seed real work refs at the revision just before the rename and read them back off the
new table name afterwards. Table literals are pinned by name, not imported from
head-of-tree ``schema.py``, since a rename test must hold both names still."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner

pytestmark = pytest.mark.component

_BEFORE = "20260725_1200_hub_graph_checks_gating"  # the head just before the rename
_RENAME = "20260726_1200_hub_chunk_work_refs_rename"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_GRAPHS = sa.Table(
    "graphs",
    sa.MetaData(),
    sa.Column("graph_id", sa.String, primary_key=True),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("entry_node_id", sa.String, nullable=False),
    sa.Column("definition_yaml", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
)

_CHUNKS = sa.Table(
    "chunks",
    sa.MetaData(),
    sa.Column("chunk_id", sa.String, primary_key=True),
    sa.Column("graph_id", sa.String, nullable=False),
    sa.Column("minted_at", sa.DateTime, nullable=False),
)


def _refs_table(name: str) -> sa.Table:
    """The ``{source, ref}`` shape under ``name`` — identical either side of the rename,
    which is the point: this revision moves the name and touches nothing else."""
    return sa.Table(
        name,
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("chunk_id", sa.String, nullable=False),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("ref", sa.String, nullable=False),
    )


_OLD = _refs_table("chunk_pm_pointers")
_NEW = _refs_table("chunk_work_refs")

# One row per branch the ``20260716_1512`` backfill could produce: the GitHub-issue
# shape that names the live hub's configured source, and a verbatim-copied non-issue row.
_SEEDED = {
    "ch_issue": ("blizzard", "26"),
    "ch_other": ("jira", "https://jira.example/PROJ-9"),
}


def _seed(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.insert(_GRAPHS).values(
                graph_id="gr_1", name="g", entry_node_id="nd_1", definition_yaml="", created_at=_T0
            )
        )
        for chunk_id, (source, ref) in _SEEDED.items():
            conn.execute(sa.insert(_CHUNKS).values(chunk_id=chunk_id, graph_id="gr_1", minted_at=_T0))
            conn.execute(sa.insert(_OLD).values(chunk_id=chunk_id, source=source, ref=ref))


def _seeded_store(tmp_path: Path) -> tuple[str, MigrationRunner]:
    """A store at ``_BEFORE`` carrying ``_SEEDED``, plus its migration runner. Every
    read below opens its own short-lived engine, since a pooled connection opened
    before a DDL rename keeps serving the schema it first saw."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)
    engine.dispose()
    return db_url, runner


def _rows(db_url: str, table: sa.Table) -> dict[str, tuple[str, str]]:
    with create_engine_from_url(db_url).connect() as conn:
        return {r.chunk_id: (r.source, r.ref) for r in conn.execute(sa.select(table))}


def _tables(db_url: str) -> set[str]:
    with create_engine_from_url(db_url).connect() as conn:
        return set(sa.inspect(conn).get_table_names())


def test_existing_rows_survive_the_rename_unchanged(tmp_path: Path) -> None:
    db_url, runner = _seeded_store(tmp_path)

    runner.upgrade(_RENAME)

    assert _rows(db_url, _NEW) == _SEEDED


def test_the_old_table_name_is_gone_after_the_rename(tmp_path: Path) -> None:
    """A rename, not a copy — leaving both would let a stale reader keep writing rows the
    hub no longer reads."""
    db_url, runner = _seeded_store(tmp_path)

    runner.upgrade(_RENAME)

    assert "chunk_work_refs" in _tables(db_url)
    assert "chunk_pm_pointers" not in _tables(db_url)


def test_the_chunk_id_foreign_key_survives_the_rename(tmp_path: Path) -> None:
    """``bzh:sql-portable`` — the renamed table is the same schema, FK included, so a
    postgres store built from the same chain is not a second schema."""
    db_url, runner = _seeded_store(tmp_path)

    runner.upgrade(_RENAME)

    with create_engine_from_url(db_url).connect() as conn:
        fks = sa.inspect(conn).get_foreign_keys("chunk_work_refs")
    assert any(fk["referred_table"] == "chunks" and fk["constrained_columns"] == ["chunk_id"] for fk in fks), (
        f"expected a chunk_id -> chunks.chunk_id foreign key, got {fks!r}"
    )


def test_downgrade_restores_the_old_name_with_its_rows(tmp_path: Path) -> None:
    db_url, runner = _seeded_store(tmp_path)
    runner.upgrade(_RENAME)

    runner.downgrade(_BEFORE)

    assert _rows(db_url, _OLD) == _SEEDED


def test_a_second_upgrade_pass_over_a_store_at_head_is_a_no_op(tmp_path: Path) -> None:
    """Re-running `upgrade` on an already-migrated store neither fails nor disturbs
    rows — alembic never re-enters `upgrade()` here; the down-then-up test below does."""
    db_url, runner = _seeded_store(tmp_path)
    runner.upgrade(_RENAME)

    runner.upgrade("head")

    assert _rows(db_url, _NEW) == _SEEDED


def test_down_then_up_returns_the_rows_under_the_new_name(tmp_path: Path) -> None:
    """The cycle that genuinely re-enters `upgrade()` — and so actually exercises the
    table-name guard, with the old name present and the new one absent, exactly the state
    the guard tests for. Rows survive both crossings."""
    db_url, runner = _seeded_store(tmp_path)
    runner.upgrade(_RENAME)

    runner.downgrade(_BEFORE)
    runner.upgrade(_RENAME)

    assert _rows(db_url, _NEW) == _SEEDED
    assert "chunk_pm_pointers" not in _tables(db_url)


def test_a_fresh_store_reaches_head_with_only_the_new_name(tmp_path: Path) -> None:
    """A `base -> head` store and an in-place-upgraded one land at the same one table."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")

    assert "chunk_work_refs" in _tables(db_url)
    assert "chunk_pm_pointers" not in _tables(db_url)
