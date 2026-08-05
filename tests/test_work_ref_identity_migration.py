"""The pointer-identity revision — a work ref's ``{provider, url}`` -> ``{source, ref}`` reshape.

Exercises the backfill on a store migrated to the revision just before the reshape: an
issue-shaped GitHub URL backfills to the repo tail plus issue number, a non-issue-shaped
row survives verbatim; also exercises ``downgrade()``'s canonicalizing reverse. Seeded
with literal ``sa.Table`` shapes, never head-of-tree ``schema.py``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner

pytestmark = pytest.mark.component

_BEFORE = "20260716_1511_hub_runner_local_pause"  # the head just before the pointer reshape
# Pinned to the reshape revision itself, never ``head``: a later rename (issue #55)
# renames the table out from under the literals above.
_RESHAPE = "20260716_1512_hub_pm_pointer_source_ref"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)

# Literal, revision-pinned table shapes — the pre-reshape ``chunk_pm_pointers`` plus the
# untouched ``graphs``/``chunks`` tables a seeded pointer row needs for its FK.
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

_OLD_POINTERS = sa.Table(
    "chunk_pm_pointers",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("provider", sa.String, nullable=False),
    sa.Column("url", sa.String, nullable=False),
)

_NEW_POINTERS = sa.Table(
    "chunk_pm_pointers",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("source", sa.String, nullable=False),
    sa.Column("ref", sa.String, nullable=False),
)


def _seed(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.insert(_GRAPHS).values(
                graph_id="gr_1", name="g", entry_node_id="nd_1", definition_yaml="", created_at=_T0
            )
        )
        conn.execute(sa.insert(_CHUNKS).values(chunk_id="ch_issue", graph_id="gr_1", minted_at=_T0))
        conn.execute(sa.insert(_CHUNKS).values(chunk_id="ch_other", graph_id="gr_1", minted_at=_T0))
        conn.execute(
            sa.insert(_OLD_POINTERS).values(
                chunk_id="ch_issue", provider="github", url="https://github.com/paul-gross/blizzard/issues/26"
            )
        )
        conn.execute(
            sa.insert(_OLD_POINTERS).values(chunk_id="ch_other", provider="jira", url="https://jira.example/PROJ-9")
        )


def test_issue_shaped_row_backfills_to_the_repo_tail(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)

    runner.upgrade(_RESHAPE)

    with engine.connect() as conn:
        rows = {r.chunk_id: r for r in conn.execute(sa.select(_NEW_POINTERS))}
    assert rows["ch_issue"].source == "blizzard"
    assert rows["ch_issue"].ref == "26"


def test_non_issue_shaped_row_survives_verbatim(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)

    runner.upgrade(_RESHAPE)

    with engine.connect() as conn:
        rows = {r.chunk_id: r for r in conn.execute(sa.select(_NEW_POINTERS))}
    assert rows["ch_other"].source == "jira"
    assert rows["ch_other"].ref == "https://jira.example/PROJ-9"


def test_downgrade_reconstructs_a_structurally_canonical_url(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)
    runner.upgrade(_RESHAPE)

    runner.downgrade(_BEFORE)

    with engine.connect() as conn:
        rows = {r.chunk_id: r for r in conn.execute(sa.select(_OLD_POINTERS))}
    # Canonicalizing, not byte-exact: the owner segment is unrecoverable from the repo
    # tail alone, so the URL carries a placeholder owner, still issue-shaped for re-parse.
    assert rows["ch_issue"].provider == "github"
    assert rows["ch_issue"].url == "https://github.com/unknown/blizzard/issues/26"
    assert rows["ch_issue"].url != "https://github.com/paul-gross/blizzard/issues/26"  # the owner is gone
    # The non-issue row round-trips exactly — it was copied verbatim both ways.
    assert rows["ch_other"].provider == "jira"
    assert rows["ch_other"].url == "https://jira.example/PROJ-9"


def test_down_then_up_returns_the_identical_source_ref_rows(tmp_path: Path) -> None:
    """The property that makes the pointer-identity revision rehearsable despite the
    lossy owner: ``(source, ref)`` identity survives a down-then-up cycle unchanged,
    since the forward rule reads only the repo tail + issue number, which survive."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)

    runner.upgrade(_RESHAPE)
    with engine.connect() as conn:
        before = {r.chunk_id: (r.source, r.ref) for r in conn.execute(sa.select(_NEW_POINTERS))}

    runner.downgrade(_BEFORE)
    runner.upgrade(_RESHAPE)
    with engine.connect() as conn:
        after = {r.chunk_id: (r.source, r.ref) for r in conn.execute(sa.select(_NEW_POINTERS))}

    assert after == before
    # Both branches specifically — the backfilled GitHub row (whose owner was lost) and
    # the verbatim-copied row (which never had one to lose).
    assert before["ch_issue"] == ("blizzard", "26")
    assert before["ch_other"] == ("jira", "https://jira.example/PROJ-9")


def test_upgrade_is_idempotent_over_an_already_reshaped_store(tmp_path: Path) -> None:
    """Re-running the revision on reshaped bytes no-ops rather than double-backfilling —
    the guard is on the revision itself, not per-row (the chunk-promoted migration's skip-rows trick doesn't
    transfer to a column reshape)."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)
    runner.upgrade(_RESHAPE)

    with engine.connect() as conn:
        first = {r.chunk_id: (r.source, r.ref) for r in conn.execute(sa.select(_NEW_POINTERS))}

    runner.upgrade(_RESHAPE)  # a second pass over the same, already-reshaped store

    with engine.connect() as conn:
        second = {r.chunk_id: (r.source, r.ref) for r in conn.execute(sa.select(_NEW_POINTERS))}
    assert second == first


def test_a_fresh_store_reaches_0013_in_the_pre_reshape_shape(tmp_path: Path) -> None:
    """The walking-skeleton revision must materialize ``{provider, url}``, not
    head-of-tree ``schema.py``'s shape — else a fresh store would silently disable the
    pointer-identity revision's ``if "url" not in columns: return`` backfill guard."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    engine = create_engine_from_url(db_url)

    def columns() -> set[str]:
        with engine.connect() as conn:
            return {c["name"] for c in sa.inspect(conn).get_columns("chunk_pm_pointers")}

    runner.upgrade("20260713_1218_hub_walking_skeleton")
    assert {"provider", "url"} <= columns(), "the walking-skeleton revision must create the pre-reshape shape"
    assert not ({"source", "ref"} & columns()), "the walking-skeleton revision leaked head-of-tree schema.py's shape"

    runner.upgrade(_RESHAPE)
    assert {"source", "ref"} <= columns(), "the pointer-identity revision must reshape a fresh store, not no-op"
    assert not ({"provider", "url"} & columns())


def test_a_fresh_store_s_work_ref_table_keeps_the_chunk_id_foreign_key(tmp_path: Path) -> None:
    """The walking-skeleton revision's frozen ``chunk_pm_pointers`` literal must still
    declare the FK to ``chunks.chunk_id`` that ``schema.py`` declares (``bzh:sql-portable``).
    Checked via ``Inspector.get_foreign_keys``, so this holds on both backends."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    engine = create_engine_from_url(db_url)

    runner.upgrade(_RESHAPE)

    with engine.connect() as conn:
        fks = sa.inspect(conn).get_foreign_keys("chunk_pm_pointers")
    assert any(fk["referred_table"] == "chunks" and fk["constrained_columns"] == ["chunk_id"] for fk in fks), (
        f"expected a chunk_id -> chunks.chunk_id foreign key, got {fks!r}"
    )
