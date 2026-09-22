"""The ``20260922_1000_review_findings`` revision against a store that already holds a
routine-sourced finding — backfilled to ``source = 'routine'``, ``routine_name`` becomes
nullable, and downgrade restores the old shape without orphaning the row (the
``tests/test_finding_facts_ref_migration.py`` shape)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from tests.support import migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_BEFORE = "20260920_1200_finding_delivered_state"  # the head just before this revision
_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_SCOPES = sa.Table(
    "scopes",
    sa.MetaData(),
    sa.Column("slug", sa.String, primary_key=True),
    sa.Column("description", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
)

# The pre-revision shape: no `source`/`severity`/`raised_by_chunk_id`; `routine_name` NOT NULL.
_OLD_FINDINGS = sa.Table(
    "findings",
    sa.MetaData(),
    sa.Column("finding_id", sa.String, primary_key=True),
    sa.Column("routine_name", sa.String, nullable=False),
    sa.Column("scope_slug", sa.String, nullable=False),
    sa.Column("class", sa.String, key="class_", nullable=False),
    sa.Column("locus", sa.String, nullable=False),
    sa.Column("summary", sa.Text, nullable=False),
)


def _seed_preexisting_finding(tmp_path: Path):  # type: ignore[no-untyped-def]
    runner, engine = migrate_to(tmp_path, _BEFORE)
    with engine.begin() as conn:
        conn.execute(sa.insert(_SCOPES).values(slug="blizzard", description="", created_at=_T0))
        seed_graph(conn, "gr_1", at=_T0)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_T0)
        conn.execute(
            sa.insert(_OLD_FINDINGS).values(
                finding_id="fin_a",
                routine_name="nightly",
                scope_slug="blizzard",
                class_="stale-docstring",
                locus="a.py:1",
                summary="s_a",
            )
        )
    return runner, engine


def test_upgrade_backfills_preexisting_rows_to_source_routine(tmp_path: Path) -> None:
    runner, _ = _seed_preexisting_finding(tmp_path)

    runner.upgrade("head")

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    with engine.connect() as conn:
        row = conn.execute(sa.text("SELECT routine_name, source, severity, raised_by_chunk_id FROM findings")).one()
    assert (row.routine_name, row.source, row.severity, row.raised_by_chunk_id) == ("nightly", "routine", None, None)


def test_upgrade_admits_a_null_routine_name_review_sourced_row(tmp_path: Path) -> None:
    runner, _ = _seed_preexisting_finding(tmp_path)
    runner.upgrade("head")
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO findings (finding_id, routine_name, scope_slug, class, locus, summary, source,"
                " severity, raised_by_chunk_id) VALUES ('fin_b', NULL, 'blizzard', 'correctness', 'a.py:1', 's',"
                " 'review', 'should-fix', 'ch_1')"
            )
        )

    with engine.connect() as conn:
        row = conn.execute(sa.text("SELECT routine_name, source FROM findings WHERE finding_id = 'fin_b'")).one()
    assert (row.routine_name, row.source) == (None, "review")


def test_downgrade_restores_the_old_shape_and_coalesces_a_null_routine_name(tmp_path: Path) -> None:
    runner, _ = _seed_preexisting_finding(tmp_path)
    runner.upgrade("head")
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO findings (finding_id, routine_name, scope_slug, class, locus, summary, source,"
                " severity, raised_by_chunk_id) VALUES ('fin_b', NULL, 'blizzard', 'correctness', 'a.py:1', 's',"
                " 'review', 'should-fix', 'ch_1')"
            )
        )

    runner.downgrade(_BEFORE)

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    with engine.connect() as conn:
        columns = {c["name"] for c in sa.inspect(engine).get_columns("findings")}
        rows = {
            r.finding_id: r.routine_name for r in conn.execute(sa.text("SELECT finding_id, routine_name FROM findings"))
        }
    assert columns == {
        "finding_id",
        "routine_name",
        "scope_slug",
        "class",
        "locus",
        "summary",
        "introduced",
        "introduced_at",
    }
    assert rows == {"fin_a": "nightly", "fin_b": ""}
