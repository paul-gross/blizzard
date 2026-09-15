"""The migrated-database prototypes ``build_hub``/``runner_migration_prototype`` cache
and copy instead of re-running every migration on each call.

Each pin below fails if the cached file ever drifts from what its own tree's
``upgrade("head")`` produces fresh — the copy is only as trustworthy as this equality."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner as hub_migration_runner
from blizzard.runner.config import RunnerConfig
from blizzard.runner.runtime import migration_runner as runner_migration_runner
from tests.runner_fakes import runner_migration_prototype
from tests.support import hub_migration_prototype


def _schema(db_url: str) -> tuple[str, set[tuple[str, str, str]]]:
    """The applied alembic revision plus every named object's DDL, order-independent —
    column order is not part of what this pins (``ADD COLUMN`` always appends)."""
    engine = create_engine_from_url(db_url)
    try:
        with engine.connect() as conn:
            version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
            rows = conn.execute(sa.text("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL")).fetchall()
        return version, {(row[0], row[1], row[2]) for row in rows}
    finally:
        engine.dispose()


@pytest.mark.component
def test_the_hub_prototype_matches_a_fresh_migration(tmp_path: Path) -> None:
    fresh_db = tmp_path / "fresh-hub.db"
    hub_migration_runner(HubConfig(root=tmp_path, db_url=f"sqlite:///{fresh_db}")).upgrade("head")

    copied_db = tmp_path / "copied-hub.db"
    shutil.copyfile(hub_migration_prototype(), copied_db)

    assert _schema(f"sqlite:///{copied_db}") == _schema(f"sqlite:///{fresh_db}")


@pytest.mark.component
def test_the_runner_prototype_matches_a_fresh_migration(tmp_path: Path) -> None:
    fresh_db = tmp_path / "fresh-runner.db"
    runner_migration_runner(RunnerConfig(root=tmp_path, db_url=f"sqlite:///{fresh_db}")).upgrade("head")

    copied_db = tmp_path / "copied-runner.db"
    shutil.copyfile(runner_migration_prototype(), copied_db)

    assert _schema(f"sqlite:///{copied_db}") == _schema(f"sqlite:///{fresh_db}")
