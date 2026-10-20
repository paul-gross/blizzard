"""The committed store documents against what a migrated store actually is.

``contracts/store/<store>.json`` is built from the store's declared schema; here a head-migrated store is
reflected through the second producer and held to it, so a revision that disagrees with
the declared schema (or a schema change with no revision) turns this red. SQLite only —
the Postgres reflection rides ``blizzard:rehearsal`` (``tests/rehearsal/``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.store import MIGRATIONS_DIR as HUB_MIGRATIONS_DIR
from blizzard.runner.store import MIGRATIONS_DIR as RUNNER_MIGRATIONS_DIR
from blizzard.tools.store_contract import Document, document_from_engine, document_from_metadata
from tests.repo_files import repo_root
from tests.runner_fakes import migrated_store_at
from tests.support import migrated_hub_at

pytestmark = pytest.mark.component

_STORES = {
    "hub": (migrated_hub_at, HUB_MIGRATIONS_DIR),
    "runner": (migrated_store_at, RUNNER_MIGRATIONS_DIR),
}


def _without_deprecated(node: object) -> object:
    """Reflection cannot see ``info={"deprecated": ...}``, so the comparison drops it."""
    if isinstance(node, dict):
        return {key: _without_deprecated(value) for key, value in node.items() if key != "deprecated"}
    if isinstance(node, list):
        return [_without_deprecated(item) for item in node]
    return node


def _committed(store: str) -> Document:
    return json.loads((repo_root() / "contracts" / "store" / f"{store}.json").read_text())


@pytest.mark.parametrize("store", sorted(_STORES))
def test_migrated_store_reflects_to_its_committed_document(store: str, tmp_path: Path) -> None:
    migrate, migrations_dir = _STORES[store]
    url = f"sqlite:///{tmp_path / f'{store}.db'}"
    migrate(url)
    engine = create_engine_from_url(url)
    try:
        applied = MigrationRunner(script_location=migrations_dir, url=url).current_revision()
        reflected = document_from_engine(store, engine, applied)
    finally:
        engine.dispose()
    assert _without_deprecated(reflected) == _without_deprecated(_committed(store))


def test_the_two_producers_tell_a_divergent_schema_apart(tmp_path: Path) -> None:
    declared = sa.MetaData()
    sa.Table("pair", declared, sa.Column("a", sa.Text, nullable=False), sa.Column("b", sa.Integer))
    built = sa.MetaData()
    sa.Table("pair", built, sa.Column("a", sa.Text, nullable=False), sa.Column("b", sa.String))
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'divergent.db'}")
    try:
        built.create_all(engine)
        reflected = document_from_engine("hub", engine, "rev")
    finally:
        engine.dispose()
    assert reflected != document_from_metadata("hub", declared, "rev")
    assert reflected == document_from_metadata("hub", built, "rev")
