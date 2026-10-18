"""An absorbed revision is distinct from an unknown newer revision."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic.util import CommandError

from blizzard.foundation.store.migrations import MigrationRunner, RevisionMismatchError, StoreBelowFloorError
from blizzard.hub.store import MIGRATIONS_DIR as HUB_MIGRATIONS_DIR
from blizzard.runner.store import MIGRATIONS_DIR

pytestmark = pytest.mark.unit


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return make_tree(tmp_path)


def make_tree(tmp_path: Path) -> Path:
    scripts = tmp_path / "scripts"
    versions = scripts / "versions"
    versions.mkdir(parents=True)
    (scripts / "env.py").write_text((MIGRATIONS_DIR / "env.py").read_text())
    (versions / "base.py").write_text(
        'revision = "base_rev"\ndown_revision = None\n'
        'BASELINE_FLOOR = "v0.2.0"\nABSORBED = frozenset({"old_rev"})\n'
        "def upgrade(): pass\ndef downgrade(): pass\n"
    )
    (versions / "next.py").write_text(
        'revision = "next_rev"\ndown_revision = "base_rev"\ndef upgrade(): pass\ndef downgrade(): pass\n'
    )
    return scripts


def _store(path: Path, revision: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(255) NOT NULL PRIMARY KEY)")
        conn.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
        conn.execute("CREATE TABLE facts (value TEXT)")
        conn.execute("INSERT INTO facts VALUES ('held')")


def _snapshot(path: Path) -> list[str]:
    with sqlite3.connect(path) as conn:
        return list(conn.iterdump())


def _journal_mode(path: Path) -> str:
    with sqlite3.connect(path) as conn:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]


def test_absorbed_revision_refuses_before_upgrade_changes_store(tree: Path, tmp_path: Path) -> None:
    store = tmp_path / "store.db"
    _store(store, "old_rev")
    runner = MigrationRunner(tree, f"sqlite:///{store}")
    assert runner.baseline_floor == "v0.2.0"
    assert runner.absorbed == frozenset({"old_rev"})
    before = _snapshot(store)
    physical = store.read_bytes()
    assert _journal_mode(store) == "delete"

    for action in (
        lambda: runner.upgrade(),
        lambda: runner.downgrade("base"),
        lambda: runner.check_current(store="runner", remedy="migrate"),
    ):
        with pytest.raises(StoreBelowFloorError) as caught:
            action()
        assert str(store) in str(caught.value)
        assert "old_rev" in str(caught.value)
        assert "v0.2.0" in str(caught.value)
        assert _snapshot(store) == before
        assert _journal_mode(store) == "delete"
        assert store.read_bytes() == physical
        assert not store.with_name(store.name + "-wal").exists()


def test_floor_error_never_discloses_database_credentials(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = MigrationRunner(tree, "postgresql://operator:secret-password@db.example:5432/records?sslmode=require")
    monkeypatch.setattr(MigrationRunner, "current_revision", lambda self: "old_rev")
    for action in (
        lambda: runner.upgrade(),
        lambda: runner.downgrade("base"),
        lambda: runner.check_current(store="hub", remedy="migrate"),
    ):
        with pytest.raises(StoreBelowFloorError) as caught:
            action()
        assert "records" in str(caught.value)
        assert "db.example" in str(caught.value)
        assert "secret-password" not in str(caught.value)
        assert "operator" not in str(caught.value)
        assert "sslmode" not in str(caught.value)


def test_known_and_unknown_revisions_keep_their_paths(tree: Path, tmp_path: Path) -> None:
    known = tmp_path / "known.db"
    _store(known, "base_rev")
    runner = MigrationRunner(tree, f"sqlite:///{known}")
    with pytest.raises(RevisionMismatchError):
        runner.check_current(store="runner", remedy="migrate")
    runner.upgrade()
    assert runner.current_revision() == "next_rev"

    unknown = tmp_path / "unknown.db"
    _store(unknown, "future_rev")
    runner = MigrationRunner(tree, f"sqlite:///{unknown}")
    before = _snapshot(unknown)
    with pytest.raises(RevisionMismatchError):
        runner.check_current(store="runner", remedy="migrate")
    with pytest.raises(CommandError):
        runner.upgrade()
    assert _snapshot(unknown) == before


@pytest.mark.parametrize("scripts", [MIGRATIONS_DIR, HUB_MIGRATIONS_DIR])
def test_shipped_trees_have_no_floor(scripts: Path, tmp_path: Path) -> None:
    runner = MigrationRunner(scripts, f"sqlite:///{tmp_path / 'store.db'}")
    assert runner.baseline_floor is None
    assert runner.absorbed == frozenset()
