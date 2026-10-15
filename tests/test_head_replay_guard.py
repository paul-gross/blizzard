"""The test-process guard keeps from-empty head replays in migration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner as hub_migration_runner
from blizzard.runner.config import RunnerConfig
from blizzard.runner.runtime import migration_runner as runner_migration_runner
from tests.support import migrate_to
from tests.test_pin_migration_prototype import test_the_hub_prototype_matches_a_fresh_migration


@pytest.mark.unit
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


@pytest.mark.component
def test_a_tail_upgrade_and_a_migration_module_replay_pass(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, "head")
    assert runner.current_revision() == runner.script_head()
    engine.dispose()
    runner.downgrade("-1")
    assert runner.current_revision() != runner.script_head()
    runner.upgrade("head")
    assert runner.current_revision() == runner.script_head()
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    test_the_hub_prototype_matches_a_fresh_migration(fresh)


@pytest.mark.unit
def test_migrate_to_preserves_an_existing_store(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, "head")
    engine.dispose()
    runner.downgrade("-1")
    current = runner.current_revision()
    again, reopened = migrate_to(tmp_path, current or "base")
    reopened.dispose()
    assert again.current_revision() == current
