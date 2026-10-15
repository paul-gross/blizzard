"""Refuse direct from-empty migration replays in ordinary test modules."""

from __future__ import annotations

import sys
from pathlib import Path

from blizzard.foundation.store.migrations import MigrationRunner
from tests.repo_files import repo_root


def install_head_replay_guard() -> None:
    """Keep migration-chain replay in migration tests and test-support builders."""
    original = MigrationRunner.upgrade
    tests = repo_root() / "tests"

    def upgrade(self: MigrationRunner, revision: str = "head") -> None:
        caller = Path(sys._getframe(1).f_code.co_filename).resolve()
        if (
            revision == "head"
            and caller.parent == tests
            and caller.name.startswith("test_")
            and caller.suffix == ".py"
            and "migration" not in caller.name
            and self.current_revision() is None
        ):
            helpers = (
                "migrated_store_at"
                if self.script_location.parent.parent.name == "runner"
                else "migrated_hub_at or migrate_to(tmp_path, 'head')"
            )
            raise AssertionError(f"{caller.name}: replaying migrations to head from empty; use {helpers}")
        original(self, revision)

    MigrationRunner.upgrade = upgrade
