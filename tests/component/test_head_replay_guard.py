"""The test-process guard keeps from-empty head replays in migration tests."""

from __future__ import annotations

from pathlib import Path

from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner as hub_migration_runner
from tests.repo_files import repo_root
from tests.support import migrate_to


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
    # A migration-named test module's from-empty replay is what the guard lets through: run the replay from a
    # caller whose module file is one, without importing a test module.
    replaying = hub_migration_runner(HubConfig(root=fresh, db_url=f"sqlite:///{fresh / 'fresh-hub.db'}"))
    module_file = str(repo_root() / "tests" / "test_migration_replay.py")
    exec(compile("runner.upgrade('head')", module_file, "exec"), {"__file__": module_file, "runner": replaying})
    assert replaying.current_revision() == replaying.script_head()
