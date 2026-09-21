"""The finding-delivered-state revision's CHECK-constraint widen (blizzard#583 Phase 1).
Seeded with a literal ``sa.Table`` shape rather than importing ``schema.py``, which now
carries the widened constraint this revision adds — the ``test_finding_exits_migration.py``
shape."""

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

_BEFORE = "20260920_1100_hub_usage_harness_provenance"  # the head just before finding_delivered_state
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_T1 = datetime(2026, 1, 2, tzinfo=UTC)

_SCOPES = sa.Table(
    "scopes",
    sa.MetaData(),
    sa.Column("slug", sa.String, primary_key=True),
    sa.Column("description", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
)

_FINDINGS = sa.Table(
    "findings",
    sa.MetaData(),
    sa.Column("finding_id", sa.String, primary_key=True),
    sa.Column("routine_name", sa.String, nullable=False),
    sa.Column("scope_slug", sa.String, nullable=False),
    sa.Column("class", sa.String, key="class_", nullable=False),
    sa.Column("locus", sa.String, nullable=False),
    sa.Column("summary", sa.Text, nullable=False),
    sa.Column("introduced", sa.String, nullable=True),
    sa.Column("introduced_at", sa.DateTime, nullable=True),
)

# The pre-finding_delivered_state shape: the nine-kind constraint, no `delivered`.
_OLD_FINDING_FACTS = sa.Table(
    "finding_facts",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("finding_id", sa.String, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("recorded_at", sa.DateTime, nullable=False),
    sa.Column("note", sa.Text, nullable=True),
    sa.Column("actor", sa.String, nullable=True),
    sa.Column("proposal_id", sa.String, nullable=True),
    sa.Column("superseded_by", sa.String, nullable=True),
)


def _seed(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(_SCOPES).values(slug="blizzard", description="", created_at=_T0))
        conn.execute(
            sa.insert(_FINDINGS).values(
                finding_id="fin_1",
                routine_name="nightly",
                scope_slug="blizzard",
                class_="stale-docstring",
                locus="a.py:1",
                summary="s",
                introduced=None,
                introduced_at=None,
            )
        )
        conn.execute(sa.insert(_OLD_FINDING_FACTS).values(finding_id="fin_1", kind="add", recorded_at=_T0, note=None))
        conn.execute(
            sa.insert(_OLD_FINDING_FACTS).values(
                finding_id="fin_1", kind="resolved", recorded_at=_T1, note="fixed upstream", actor="user:pgross"
            )
        )


def test_upgrade_admits_delivered_and_preserves_existing_rows(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)

    runner.upgrade("head")

    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(_OLD_FINDING_FACTS.c.kind, _OLD_FINDING_FACTS.c.note).order_by(_OLD_FINDING_FACTS.c.id)
        ).all()
    assert [(r.kind, r.note) for r in rows] == [("add", None), ("resolved", "fixed upstream")]

    with engine.begin() as conn:
        conn.execute(
            sa.insert(_OLD_FINDING_FACTS).values(
                finding_id="fin_1", kind="delivered", recorded_at=_T1, note=None, actor="user:pgross"
            )
        )


def test_upgrade_still_rejects_a_kind_outside_the_widened_vocabulary(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)
    runner.upgrade("head")

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(sa.insert(_OLD_FINDING_FACTS).values(finding_id="fin_1", kind="bogus", recorded_at=_T1))


def test_downgrade_narrows_the_constraint_and_preserves_existing_rows(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)
    runner.upgrade("head")

    runner.downgrade(_BEFORE)

    with engine.connect() as conn:
        kinds = conn.execute(sa.select(_OLD_FINDING_FACTS.c.kind).order_by(_OLD_FINDING_FACTS.c.id)).scalars().all()
    assert kinds == ["add", "resolved"]

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(sa.insert(_OLD_FINDING_FACTS).values(finding_id="fin_1", kind="delivered", recorded_at=_T1))


def test_downgrade_then_upgrade_round_trips(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)
    runner.upgrade("head")

    runner.downgrade(_BEFORE)
    runner.upgrade("head")

    with engine.begin() as conn:
        conn.execute(
            sa.insert(_OLD_FINDING_FACTS).values(finding_id="fin_1", kind="delivered", recorded_at=_T1, note=None)
        )
