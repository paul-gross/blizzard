"""Tests proving the ``20260906_1130_routine_scopes_join`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.store import MIGRATIONS_DIR as HUB_MIGRATIONS_DIR
from tests.support import MIGRATION_CLOCK

pytestmark = [pytest.mark.unit]

_ROUTINE_SCOPES_JOIN_PARENT = "20260905_1100_hub_runner_external_usage_slug"

_ROUTINE_SCOPES_JOIN = "20260906_1130_routine_scopes_join"


def test_routine_scopes_table_survives_migration_roundtrip(tmp_path: Path) -> None:
    """``routine_scopes`` — downgrades to this revision's own parent by
    id, so the drop half is asserted rather than inferred from a revision marker a
    no-op ``downgrade()`` would satisfy just as well."""
    config = hub_runtime.init_environment(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)

    def _table_names() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return set(sa.inspect(engine).get_table_names())
        finally:
            engine.dispose()

    assert "routine_scopes" in _table_names()

    runner.downgrade(_ROUTINE_SCOPES_JOIN_PARENT)
    assert "routine_scopes" not in _table_names()

    runner.upgrade("head")
    assert "routine_scopes" in _table_names()


def test_routine_scopes_seeds_from_findings_finding_sets_and_routine_defaults(tmp_path: Path) -> None:
    """Backfills ``routine_scopes`` from every distinct ``(routine_name, scope_slug)``
    pair ``findings``/``finding_sets`` carried plus each routine's default; an empty or
    unmatched ``routine_name`` is skipped, and a duplicate pair inserted only once."""
    url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = MigrationRunner(script_location=HUB_MIGRATIONS_DIR, url=url, clock=MIGRATION_CLOCK)
    runner.upgrade(_ROUTINE_SCOPES_JOIN_PARENT)

    engine = create_engine_from_url(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO routines (routine_id, name, graph_name, default_scope_slug, created_at)"
                    " VALUES (:routine_id, :name, 'g', :default_scope_slug, '2026-01-01 00:00:00')"
                ),
                [
                    {"routine_id": "rtn_1", "name": "nightly", "default_scope_slug": "zzz-default"},
                    {"routine_id": "rtn_2", "name": "weekly", "default_scope_slug": "weekly-default"},
                ],
            )
            conn.execute(
                sa.text(
                    "INSERT INTO findings (finding_id, routine_name, scope_slug, class, locus, summary)"
                    " VALUES (:finding_id, :routine_name, :scope_slug, 'k', 'l', 's')"
                ),
                [
                    {"finding_id": "fin_1", "routine_name": "nightly", "scope_slug": "alpha"},
                    {"finding_id": "fin_2", "routine_name": "nightly", "scope_slug": "alpha"},
                    {"finding_id": "fin_3", "routine_name": "ghost", "scope_slug": "unreachable"},
                ],
            )
            conn.execute(
                sa.text(
                    "INSERT INTO finding_sets (finding_set_id, artifact_id, chunk_id, scope_slug, routine_name,"
                    " revisions) VALUES (:finding_set_id, :artifact_id, 'ch_1', :scope_slug, :routine_name, '{}')"
                ),
                [
                    {
                        "finding_set_id": "fins_1",
                        "artifact_id": "art_1",
                        "scope_slug": "beta",
                        "routine_name": "nightly",
                    },
                    {
                        "finding_set_id": "fins_2",
                        "artifact_id": "art_2",
                        "scope_slug": "delta",
                        "routine_name": "weekly",
                    },
                    {"finding_set_id": "fins_3", "artifact_id": "art_3", "scope_slug": "gamma", "routine_name": ""},
                ],
            )
    finally:
        engine.dispose()

    runner.upgrade(_ROUTINE_SCOPES_JOIN)

    engine = create_engine_from_url(url)
    try:
        with engine.connect() as conn:
            pairs = {
                (r.routine_id, r.scope_slug)
                for r in conn.execute(sa.text("SELECT routine_id, scope_slug FROM routine_scopes")).all()
            }
    finally:
        engine.dispose()

    assert pairs == {
        ("rtn_1", "alpha"),
        ("rtn_1", "beta"),
        ("rtn_1", "zzz-default"),
        ("rtn_2", "delta"),
        ("rtn_2", "weekly-default"),
    }
