"""Pinning tests for hub decisions that were previously defended by comment alone
(``bzh:mutation-review-selection``).

Each test exists because a long comment argued for a decision no assertion covered;
the comment at each site now points back at the test that fails on revert."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import MIGRATIONS_DIR
from tests.migration_support_pin_hub_api import (
    _MIGRATION_CLOCK,
    _PM_POINTER_SOURCE_REF,
    _store,
)
from tests.support import hub_scoped_engine

_DELIVERY_PR_FACTS = "20260714_0819_hub_delivery_pr_facts"

_PR_OPENED_IDEMPOTENT = "20260716_2206_hub_pr_opened_idempotent"


def _unique_constraint_names(url: str, table: str) -> set[str]:
    engine = hub_scoped_engine(create_engine_from_url(url))
    try:
        return {c["name"] or "" for c in sa.inspect(engine).get_unique_constraints(table)}
    finally:
        engine.dispose()


_BEFORE = "20260716_1512_hub_pm_pointer_source_ref"  # the head just before the idempotent constraint

_AFTER = "20260716_2206_hub_pr_opened_idempotent"  # the revision under test; later heads drop the table

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


@pytest.mark.unit
def test_delivery_pr_opened_gains_its_uniqueness_only_at_the_revision_that_adds_it(tmp_path: Path) -> None:
    """``0014`` is the one revision that constrains (chunk_id, repo); the revision that
    *creates* the table must not already carry the constraint off ``src/blizzard/hub/store/schema.py``."""
    _runner, url = _store(tmp_path, _DELIVERY_PR_FACTS)
    assert _unique_constraint_names(url, "delivery_pr_opened") == set()

    MigrationRunner(script_location=MIGRATIONS_DIR, url=url, clock=_MIGRATION_CLOCK).upgrade(_PR_OPENED_IDEMPOTENT)
    assert "uq_delivery_pr_opened_chunk_repo" in _unique_constraint_names(url, "delivery_pr_opened")


@pytest.mark.unit
def test_pr_opened_upgrade_keeps_only_the_earliest_duplicate(tmp_path: Path) -> None:
    """A store carrying the dogfood duplicates upgrades rather than failing the
    constraint add: every row but the earliest (lowest ``id``) per (chunk_id, repo) is
    deleted, and a distinct (chunk_id, repo) pair is untouched."""
    _runner, url = _store(tmp_path, _PM_POINTER_SOURCE_REF)
    engine = hub_scoped_engine(create_engine_from_url(url))
    insert = sa.text(
        "INSERT INTO delivery_pr_opened (id, chunk_id, repo, pr_number, pr_url, commit_hash, opened_at)"
        " VALUES (:id, :chunk_id, :repo, :n, 'http://pr', 'abc', '2026-01-01 00:00:00')"
    )
    try:
        with engine.begin() as conn:
            conn.execute(insert, {"id": 1, "chunk_id": "ch_1", "repo": "r", "n": 1})
            conn.execute(insert, {"id": 2, "chunk_id": "ch_1", "repo": "r", "n": 2})
            conn.execute(insert, {"id": 3, "chunk_id": "ch_2", "repo": "r", "n": 3})
    finally:
        engine.dispose()

    MigrationRunner(script_location=MIGRATIONS_DIR, url=url, clock=_MIGRATION_CLOCK).upgrade(_PR_OPENED_IDEMPOTENT)

    engine = hub_scoped_engine(create_engine_from_url(url))
    try:
        with engine.connect() as conn:
            surviving = sorted(r.id for r in conn.execute(sa.text("SELECT id FROM delivery_pr_opened")).all())
    finally:
        engine.dispose()
    assert surviving == [1, 3]


@pytest.mark.component
def test_upgrade_deduplicates_existing_duplicate_rows(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = hub_scoped_engine(create_engine_from_url(db_url))
    _seed(engine)

    runner.upgrade(_AFTER)

    with engine.connect() as conn:
        rows = conn.execute(sa.select(_NEW_PR_OPENED.c.id, _NEW_PR_OPENED.c.chunk_id, _NEW_PR_OPENED.c.repo)).all()

    # Exactly one row per (chunk, repo): the duplicate is gone, the distinct repo kept.
    assert sorted((r.chunk_id, r.repo) for r in rows) == [("ch_1", "acme/other"), ("ch_1", "acme/widget")]
    # The earliest-written row survives (id=1, not the racing duplicate id=2).
    surviving_ids = {r.id for r in rows}
    assert surviving_ids == {1, 3}


@pytest.mark.component
def test_upgrade_enforces_the_constraint_going_forward(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = hub_scoped_engine(create_engine_from_url(db_url))
    _seed(engine)
    runner.upgrade(_AFTER)

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


@pytest.mark.component
def test_downgrade_then_upgrade_round_trips_the_deduplicated_rows(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = hub_scoped_engine(create_engine_from_url(db_url))
    _seed(engine)
    runner.upgrade(_AFTER)

    runner.downgrade(_BEFORE)
    runner.upgrade(_AFTER)

    with engine.connect() as conn:
        rows = conn.execute(sa.select(_NEW_PR_OPENED.c.chunk_id, _NEW_PR_OPENED.c.repo)).all()
    assert sorted((r.chunk_id, r.repo) for r in rows) == [("ch_1", "acme/other"), ("ch_1", "acme/widget")]
