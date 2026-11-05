"""The test-process guard keeps from-empty head replays in migration tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner as hub_migration_runner
from blizzard.runner.config import RunnerConfig
from blizzard.runner.runtime import migration_runner as runner_migration_runner
from tests.support.repo_files import repo_root
from tests.support.scaffolding import hub_migration_prototype, migrate_to


@pytest.mark.parametrize("tree, helper", [("hub", "migrated_hub_at"), ("runner", "migrated_store_at")])
def test_an_ordinary_test_cannot_replay_from_empty(tmp_path: Path, tree: str, helper: str) -> None:
    url = f"sqlite:///{tmp_path / 'store.db'}"
    runner = (
        hub_migration_runner(HubConfig(root=tmp_path, db_url=url))
        if tree == "hub"
        else runner_migration_runner(RunnerConfig(root=tmp_path, db_url=url))
    )
    with pytest.raises(AssertionError, match=f"test_head_replay_guard.py.*{helper}"):
        runner.upgrade("head")
    assert runner.current_revision() is None


@pytest.mark.parametrize("folder", ["unit", "component", "guards", "unit/hub"])
def test_a_module_under_a_tier_folder_cannot_replay_from_empty(tmp_path: Path, folder: str) -> None:
    url = f"sqlite:///{tmp_path / 'store.db'}"
    runner = hub_migration_runner(HubConfig(root=tmp_path, db_url=url))
    module_file = repo_root() / "tests" / folder / "test_replays.py"
    namespace = {"__file__": str(module_file), "runner": runner}
    with pytest.raises(AssertionError, match=r"test_replays\.py.*migrated_hub_at"):
        exec(compile("runner.upgrade('head')", str(module_file), "exec"), namespace)


def test_a_frame_resolving_to_the_tests_dir_itself_is_not_a_test_module(tmp_path: Path) -> None:
    from tests.support.head_replay_guard import _in_test_tree  # pyright: ignore[reportPrivateUsage]

    assert not _in_test_tree(tmp_path, tmp_path)


def test_a_runner_named_ancestor_does_not_turn_a_hub_tree_into_a_runner_tree(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'store.db'}"
    runner = replace(
        hub_migration_runner(HubConfig(root=tmp_path, db_url=url)),
        script_location=Path("/home/runner/work/blizzard/src/blizzard/hub/store/migrations"),
    )
    with pytest.raises(AssertionError, match=r"test_head_replay_guard\.py.*migrated_hub_at"):
        runner.upgrade("head")


def test_migrate_to_copies_a_fresh_head_store_without_replaying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prototype = hub_migration_prototype()
    db = tmp_path / "hub.db"
    assert not db.exists()

    def refuse_replay(self: MigrationRunner, revision: str = "head") -> None:
        pytest.fail(f"migrate_to replayed {revision} instead of copying the prototype")

    with monkeypatch.context() as patch:
        patch.setattr(MigrationRunner, "upgrade", refuse_replay)
        runner, engine = migrate_to(tmp_path, "head")
    try:
        assert db.read_bytes() == prototype.read_bytes()
        assert runner.current_revision() == runner.script_head()
    finally:
        engine.dispose()

    runner.downgrade("-1")
    assert runner.current_revision() != runner.script_head()
    runner.upgrade("head")
    assert runner.current_revision() == runner.script_head()


def test_migrate_to_preserves_an_existing_store(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, "head")
    engine.dispose()
    runner.downgrade("-1")
    current = runner.current_revision()
    again, reopened = migrate_to(tmp_path, current or "base")
    reopened.dispose()
    assert again.current_revision() == current
