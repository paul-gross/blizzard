"""The finding-exits revision's CHECK-constraint widen and new columns (blizzard#394
Phase 1). Seeded with literal ``sa.Table`` shapes rather than importing ``schema.py``,
which now carries the widened constraint and columns this revision adds — the
``tests/test_pr_opened_migration.py`` shape."""

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

_BEFORE = "20260831_0945_garden_proposal_closures"  # the head just before finding_exits
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_T1 = datetime(2026, 1, 2, tzinfo=UTC)

_SCOPES = sa.Table(
    "scopes",
    sa.MetaData(),
    sa.Column("slug", sa.String, primary_key=True),
    sa.Column("description", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
)

# The pre-finding_exits shape: no introduced_at.
_OLD_FINDINGS = sa.Table(
    "findings",
    sa.MetaData(),
    sa.Column("finding_id", sa.String, primary_key=True),
    sa.Column("routine_name", sa.String, nullable=False),
    sa.Column("scope_slug", sa.String, nullable=False),
    sa.Column("class", sa.String, key="class_", nullable=False),
    sa.Column("locus", sa.String, nullable=False),
    sa.Column("summary", sa.Text, nullable=False),
    sa.Column("introduced", sa.String, nullable=True),
)

# The pre-finding_exits shape: no actor/proposal_id/superseded_by, the 3-kind constraint.
_OLD_FINDING_FACTS = sa.Table(
    "finding_facts",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("finding_id", sa.String, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("recorded_at", sa.DateTime, nullable=False),
    sa.Column("note", sa.Text, nullable=True),
)

# The post-finding_exits shape, for asserting the widened constraint and new columns.
_NEW_FINDINGS = sa.Table(
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

_NEW_FINDING_FACTS = sa.Table(
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


def _seed_old_shape(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(_SCOPES).values(slug="blizzard", description="", created_at=_T0))
        conn.execute(
            sa.insert(_OLD_FINDINGS).values(
                finding_id="fin_1",
                routine_name="nightly",
                scope_slug="blizzard",
                class_="stale-docstring",
                locus="a.py:1",
                summary="s",
                introduced=None,
            )
        )
        conn.execute(sa.insert(_OLD_FINDING_FACTS).values(finding_id="fin_1", kind="add", recorded_at=_T0, note=None))
        conn.execute(
            sa.insert(_OLD_FINDING_FACTS).values(
                finding_id="fin_1", kind="gone", recorded_at=_T1, note="no longer reproduces"
            )
        )


def test_upgrade_widens_the_constraint_and_adds_the_new_columns(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed_old_shape(engine)

    runner.upgrade("head")

    with engine.connect() as conn:
        columns = {c["name"] for c in sa.inspect(conn).get_columns("finding_facts")}
        finding_columns = {c["name"] for c in sa.inspect(conn).get_columns("findings")}
    assert {"actor", "proposal_id", "superseded_by"} <= columns
    assert "introduced_at" in finding_columns

    # Old rows still read fine — untouched by the recreate.
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(_NEW_FINDING_FACTS.c.kind, _NEW_FINDING_FACTS.c.note, _NEW_FINDING_FACTS.c.actor).order_by(
                _NEW_FINDING_FACTS.c.id
            )
        ).all()
        finding = conn.execute(
            sa.select(_NEW_FINDINGS.c.introduced, _NEW_FINDINGS.c.introduced_at).where(
                _NEW_FINDINGS.c.finding_id == "fin_1"
            )
        ).one()
    assert [(r.kind, r.note, r.actor) for r in rows] == [
        ("add", None, None),
        ("gone", "no longer reproduces", None),
    ]
    assert finding.introduced is None
    assert finding.introduced_at is None

    # A widened kind is now accepted, carrying the new columns.
    with engine.begin() as conn:
        conn.execute(
            sa.insert(_NEW_FINDING_FACTS).values(
                finding_id="fin_1",
                kind="resolved",
                recorded_at=_T1,
                note="fixed upstream",
                actor="user:pgross",
                proposal_id=None,
                superseded_by=None,
            )
        )


def test_upgrade_rejects_a_kind_outside_the_widened_vocabulary(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed_old_shape(engine)
    runner.upgrade("head")

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(sa.insert(_NEW_FINDING_FACTS).values(finding_id="fin_1", kind="bogus", recorded_at=_T1))


def test_downgrade_reverses_the_columns_and_narrows_the_constraint(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed_old_shape(engine)
    runner.upgrade("head")

    runner.downgrade(_BEFORE)

    with engine.connect() as conn:
        columns = {c["name"] for c in sa.inspect(conn).get_columns("finding_facts")}
        finding_columns = {c["name"] for c in sa.inspect(conn).get_columns("findings")}
    assert not {"actor", "proposal_id", "superseded_by"} & columns
    assert "introduced_at" not in finding_columns

    # The old rows still read fine after the round trip.
    with engine.connect() as conn:
        kinds = conn.execute(sa.select(_OLD_FINDING_FACTS.c.kind).order_by(_OLD_FINDING_FACTS.c.id)).scalars().all()
    assert kinds == ["add", "gone"]

    # The widened vocabulary no longer validates.
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(sa.insert(_OLD_FINDING_FACTS).values(finding_id="fin_1", kind="resolved", recorded_at=_T1))


def test_downgrade_then_upgrade_round_trips(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed_old_shape(engine)
    runner.upgrade("head")

    runner.downgrade(_BEFORE)
    runner.upgrade("head")

    with engine.connect() as conn:
        columns = {c["name"] for c in sa.inspect(conn).get_columns("finding_facts")}
    assert {"actor", "proposal_id", "superseded_by"} <= columns
