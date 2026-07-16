"""The 0012 pointer-identity reshape — ``{provider, url}`` -> ``{source, ref}`` (D-105).

Exercises the backfill on a store migrated to the revision *before* 0012, seeded with
rows in the **pre-0012** shape: an issue-shaped GitHub URL (backfills to the repo tail
plus the issue number) and a non-issue-shaped row (survives verbatim into ``ref``, D-105's
lossless branch). Also exercises ``downgrade()``'s canonicalizing reverse.

Seeded with literal SQL against the pre-0012 shape rather than
``from blizzard.hub.store import schema as s`` (the way
``test_chunk_promoted_migration.py`` seeds its unaffected tables) — the same reason
migration 0012 itself declares its own local ``sa.Table`` literals rather than
importing ``schema.py`` (see that module's docstring): ``schema.py`` is head-of-tree
and will keep moving, so a test pinned to a revision *before* a column reshape must not
import a table shape that has since moved on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner

pytestmark = pytest.mark.component

_BEFORE = "0011_hub_chunk_promoted"  # the head just before the pointer reshape
_T0 = datetime(2026, 1, 1, tzinfo=UTC)

# Literal, revision-pinned table shapes — the pre-0012 ``chunk_pm_pointers`` plus the
# untouched ``graphs``/``chunks`` tables this revision doesn't reshape, but a seeded
# pointer row still needs a parent chunk to satisfy the foreign key.
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

    runner.upgrade("head")

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

    runner.upgrade("head")

    with engine.connect() as conn:
        rows = {r.chunk_id: r for r in conn.execute(sa.select(_NEW_POINTERS))}
    assert rows["ch_other"].source == "jira"
    assert rows["ch_other"].ref == "https://jira.example/PROJ-9"


def test_downgrade_reconstructs_a_resolvable_url(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)
    runner.upgrade("head")

    runner.downgrade(_BEFORE)

    with engine.connect() as conn:
        rows = {r.chunk_id: r for r in conn.execute(sa.select(_OLD_POINTERS))}
    # Canonicalizing, not byte-exact (D-105): the owner segment is unrecoverable from
    # the repo tail alone, so the reconstructed URL carries a documented placeholder
    # owner rather than the original ``paul-gross`` — but it is a resolvable GitHub
    # issue URL for the same repo tail and issue number.
    assert rows["ch_issue"].provider == "github"
    assert rows["ch_issue"].url.endswith("/blizzard/issues/26")
    assert rows["ch_issue"].url.startswith("https://github.com/")
    # The non-issue row round-trips exactly — it was copied verbatim both ways.
    assert rows["ch_other"].provider == "jira"
    assert rows["ch_other"].url == "https://jira.example/PROJ-9"
