"""The chunk-repository-targets revision: ``chunk_repository_targets`` is created as
``src/blizzard/hub/store/schema.py`` declares it; a re-run changes nothing; and the downgrade restores the parent store
exactly, after which the upgrade runs again."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261111_0900_repository_link_base_branch"
_REVISION = "20261111_1000_chunk_repository_targets"
_TABLE = "chunk_repository_targets"


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return schema_snapshot(engine, exemptions={}, name_exemptions={})


def _shape(engine: sa.Engine) -> tuple[dict[str, bool], list[tuple[str, ...]]]:
    inspector = sa.inspect(engine)
    columns = {c["name"]: bool(c["nullable"]) for c in inspector.get_columns(_TABLE)}
    uniques = sorted(tuple(u["column_names"]) for u in inspector.get_unique_constraints(_TABLE))
    return columns, uniques


def test_the_table_matches_the_declared_schema(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, _REVISION)
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        assert _shape(engine) == _shape(declared)
    finally:
        declared.dispose()


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _REVISION)
    schema = _snapshot(engine)
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_REVISION)

    assert _snapshot(engine) == schema


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = _snapshot(engine)
    runner.upgrade(_REVISION)

    runner.downgrade(_PARENT)

    assert _snapshot(engine) == before
    runner.upgrade(_REVISION)
    assert sa.inspect(engine).has_table(_TABLE)
