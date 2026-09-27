"""The ``20260926_1000_operator_garden_proposals`` revision (blizzard#631 D1) against a
store that already holds a routine-run garden proposal — backfilled to
``origin = 'routine-run'``, `routine_name` becomes nullable, the check constraints refuse
an inconsistent row, and downgrade restores the old shape without orphaning the row (the
``tests/test_review_findings_migration.py`` shape)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.engine import create_engine_from_url
from tests.support import migrate_to

pytestmark = pytest.mark.component

_BEFORE = "20260923_1000_routine_lifecycle_facts"  # the head just before this revision
_T0 = datetime(2026, 1, 1, tzinfo=UTC)

# The pre-revision shape: no `origin`/`created_by`; `routine_name` NOT NULL.
_OLD_GARDEN_PROPOSALS = sa.Table(
    "garden_proposals",
    sa.MetaData(),
    sa.Column("proposal_id", sa.String, primary_key=True),
    sa.Column("routine_name", sa.String, nullable=False),
    sa.Column("class", sa.String, key="class_", nullable=False),
    sa.Column("title", sa.String, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
)


def _seed_preexisting_proposal(tmp_path: Path):  # type: ignore[no-untyped-def]
    runner, engine = migrate_to(tmp_path, _BEFORE)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(_OLD_GARDEN_PROPOSALS).values(
                proposal_id="gprop_a",
                routine_name="nightly",
                class_="stale-docstring",
                title="t",
                body="b",
                created_at=_T0,
            )
        )
    return runner, engine


def test_upgrade_backfills_preexisting_rows_to_origin_routine_run(tmp_path: Path) -> None:
    runner, _ = _seed_preexisting_proposal(tmp_path)

    runner.upgrade("head")

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    with engine.connect() as conn:
        row = conn.execute(sa.text("SELECT routine_name, origin, created_by FROM garden_proposals")).one()
    assert (row.routine_name, row.origin, row.created_by) == ("nightly", "routine-run", None)


def test_upgrade_admits_a_null_routine_name_operator_row(tmp_path: Path) -> None:
    runner, _ = _seed_preexisting_proposal(tmp_path)
    runner.upgrade("head")
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO garden_proposals (proposal_id, routine_name, class, title, body, created_at, origin,"
                " created_by) VALUES ('gprop_b', NULL, 'idea', 't', 'b', :at, 'operator', 'u_1')"
            ),
            {"at": _T0},
        )

    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT routine_name, origin, created_by FROM garden_proposals WHERE proposal_id = 'gprop_b'")
        ).one()
    assert (row.routine_name, row.origin, row.created_by) == (None, "operator", "u_1")


def test_upgrade_refuses_a_routine_run_row_with_no_routine_name(tmp_path: Path) -> None:
    runner, _ = _seed_preexisting_proposal(tmp_path)
    runner.upgrade("head")
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO garden_proposals (proposal_id, routine_name, class, title, body, created_at,"
                    " origin, created_by) VALUES ('gprop_c', NULL, 'idea', 't', 'b', :at, 'routine-run', NULL)"
                ),
                {"at": _T0},
            )


def test_upgrade_refuses_an_operator_row_with_no_created_by(tmp_path: Path) -> None:
    runner, _ = _seed_preexisting_proposal(tmp_path)
    runner.upgrade("head")
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO garden_proposals (proposal_id, routine_name, class, title, body, created_at,"
                    " origin, created_by) VALUES ('gprop_d', NULL, 'idea', 't', 'b', :at, 'operator', NULL)"
                ),
                {"at": _T0},
            )


def test_downgrade_restores_the_old_shape_and_coalesces_a_null_routine_name(tmp_path: Path) -> None:
    runner, _ = _seed_preexisting_proposal(tmp_path)
    runner.upgrade("head")
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO garden_proposals (proposal_id, routine_name, class, title, body, created_at, origin,"
                " created_by) VALUES ('gprop_b', NULL, 'idea', 't', 'b', :at, 'operator', 'u_1')"
            ),
            {"at": _T0},
        )

    runner.downgrade(_BEFORE)

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    with engine.connect() as conn:
        columns = {c["name"] for c in sa.inspect(engine).get_columns("garden_proposals")}
        rows = {
            r.proposal_id: r.routine_name
            for r in conn.execute(sa.text("SELECT proposal_id, routine_name FROM garden_proposals"))
        }
    assert columns == {
        "proposal_id",
        "routine_name",
        "class",
        "title",
        "body",
        "created_at",
        "source_artifact_id",
        "ref",
    }
    assert rows == {"gprop_a": "nightly", "gprop_b": ""}
