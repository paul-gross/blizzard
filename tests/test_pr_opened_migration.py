"""The pr-opened-idempotent revision's ``pr.opened`` de-duplication + unique constraint (issue #10).

Exercises the migration on a store carrying the exact shape a dogfood run produced:
two ``delivery_pr_opened`` rows for the same (chunk, repo) — the coordinator's
read-then-write race on its DB-backed skip-set (a store read each call, not an
in-memory cache — ``blizzard.hub.delivery.coordinator``). Seeded with literal
``sa.Table`` shapes rather than ``from blizzard.hub.store import schema as s`` — the
same reason ``test_pm_pointer_migration.py`` does (see that module's docstring):
``schema.py`` now carries the unique constraint this revision adds, so a test pinned to
the revision *before* it must not import a table shape that has already moved on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner

pytestmark = pytest.mark.component

_BEFORE = "20260716_1512_hub_pm_pointer_source_ref"  # the head just before the idempotent constraint
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

# The pre-pr-opened-idempotent shape: no unique constraint, so two rows for the same (chunk_id, repo)
# are legal to seed — exactly the dogfood run's duplicate.
_OLD_PR_OPENED = sa.Table(
    "delivery_pr_opened",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("repo", sa.String, nullable=False),
    sa.Column("pr_number", sa.Integer, nullable=False),
    sa.Column("pr_url", sa.String, nullable=False),
    sa.Column("commit_hash", sa.String, nullable=False),
    sa.Column("opened_at", sa.DateTime, nullable=False),
)

# The post-pr-opened-idempotent shape, for asserting the constraint now holds.
_NEW_PR_OPENED = sa.Table(
    "delivery_pr_opened",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("repo", sa.String, nullable=False),
    sa.Column("pr_number", sa.Integer, nullable=False),
    sa.Column("pr_url", sa.String, nullable=False),
    sa.Column("commit_hash", sa.String, nullable=False),
    sa.Column("opened_at", sa.DateTime, nullable=False),
    sa.UniqueConstraint("chunk_id", "repo"),
)


def _seed(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.insert(_GRAPHS).values(
                graph_id="gr_1", name="g", entry_node_id="nd_1", definition_yaml="", created_at=_T0
            )
        )
        conn.execute(sa.insert(_CHUNKS).values(chunk_id="ch_1", graph_id="gr_1", minted_at=_T0))
        # The dogfood duplicate: two rows, same (chunk, repo), the coordinator's race.
        conn.execute(
            sa.insert(_OLD_PR_OPENED).values(
                id=1,
                chunk_id="ch_1",
                repo="acme/widget",
                pr_number=1,
                pr_url="http://forge/acme/widget/pull/1",
                commit_hash="abc123",
                opened_at=_T0,
            )
        )
        conn.execute(
            sa.insert(_OLD_PR_OPENED).values(
                id=2,
                chunk_id="ch_1",
                repo="acme/widget",
                pr_number=1,
                pr_url="http://forge/acme/widget/pull/1",
                commit_hash="abc123",
                opened_at=_T0,
            )
        )
        # A distinct repo on the same chunk — untouched by the de-dupe.
        conn.execute(
            sa.insert(_OLD_PR_OPENED).values(
                id=3,
                chunk_id="ch_1",
                repo="acme/other",
                pr_number=2,
                pr_url="http://forge/acme/other/pull/2",
                commit_hash="def456",
                opened_at=_T0,
            )
        )


def test_upgrade_deduplicates_existing_duplicate_rows(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)

    runner.upgrade("head")

    with engine.connect() as conn:
        rows = conn.execute(sa.select(_NEW_PR_OPENED.c.id, _NEW_PR_OPENED.c.chunk_id, _NEW_PR_OPENED.c.repo)).all()

    # Exactly one row per (chunk, repo): the duplicate is gone, the distinct repo kept.
    assert sorted((r.chunk_id, r.repo) for r in rows) == [("ch_1", "acme/other"), ("ch_1", "acme/widget")]
    # The earliest-written row survives (id=1, not the racing duplicate id=2).
    surviving_ids = {r.id for r in rows}
    assert surviving_ids == {1, 3}


def test_upgrade_enforces_the_constraint_going_forward(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)
    runner.upgrade("head")

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            sa.insert(_NEW_PR_OPENED).values(
                chunk_id="ch_1",
                repo="acme/widget",
                pr_number=99,
                pr_url="http://forge/acme/widget/pull/99",
                commit_hash="zzz",
                opened_at=_T0,
            )
        )


def test_downgrade_then_upgrade_round_trips_the_deduplicated_rows(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)
    runner.upgrade("head")

    runner.downgrade(_BEFORE)
    runner.upgrade("head")

    with engine.connect() as conn:
        rows = conn.execute(sa.select(_NEW_PR_OPENED.c.chunk_id, _NEW_PR_OPENED.c.repo)).all()
    assert sorted((r.chunk_id, r.repo) for r in rows) == [("ch_1", "acme/other"), ("ch_1", "acme/widget")]
