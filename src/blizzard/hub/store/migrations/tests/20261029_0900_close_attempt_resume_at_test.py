"""The close-attempt-resume-at revision: ``close_intent_attempts`` gains a nullable ``resume_at``; a re-run changes
nothing; and the downgrade drops the ``deferred`` rows and restores the store exactly."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from tests.migration_support_store_migrations import schema_snapshot
from tests.support import migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261027_0900_flows"

_RESUME_AT = "20261029_0900_close_attempt_resume_at"


def _columns(engine: sa.Engine) -> dict[str, bool]:
    return {c["name"]: bool(c["nullable"]) for c in sa.inspect(engine).get_columns("close_intent_attempts")}


def test_the_upgrade_adds_a_nullable_resume_at(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)

    runner.upgrade(_RESUME_AT)

    assert _columns(engine)["resume_at"] is True


def test_the_new_column_matches_the_declared_schema(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        assert _columns(engine)["resume_at"] == _columns(declared)["resume_at"]
    finally:
        declared.dispose()


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _RESUME_AT)
    schema = schema_snapshot(engine, exemptions={}, name_exemptions={})
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_RESUME_AT)

    assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = schema_snapshot(engine, exemptions={}, name_exemptions={})
    runner.upgrade(_RESUME_AT)

    runner.downgrade(_PARENT)

    assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == before
    runner.upgrade(_RESUME_AT)
    assert "resume_at" in _columns(engine)


def test_the_downgrade_drops_deferred_attempts_and_keeps_the_rest(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _RESUME_AT)
    at = datetime(2026, 10, 29, tzinfo=UTC).isoformat()
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        for outcome in ("failed", "skipped", "deferred"):
            conn.execute(
                sa.text(
                    "INSERT INTO close_intent_attempts (intent_id, attempted_at, outcome, tenant_id, resume_at)"
                    " VALUES (1, :at, :outcome, 't', :resume)"
                ),
                {"at": at, "outcome": outcome, "resume": at if outcome == "deferred" else None},
            )

    runner.downgrade(_PARENT)

    with engine.connect() as conn:
        outcomes = sorted(conn.execute(sa.text("SELECT outcome FROM close_intent_attempts")).scalars())
    assert outcomes == ["failed", "skipped"]
