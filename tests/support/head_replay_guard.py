"""Refuse direct from-empty migration replays in ordinary test modules."""

from __future__ import annotations

import inspect
from pathlib import Path

from blizzard.foundation.store.migrations import MigrationRunner
from tests.support.repo_files import repo_root

#: The folders under ``tests/`` whose modules the guard covers beside the ones directly at the root.
_TIER_FOLDERS = ("unit", "component", "guards")


def _in_test_tree(caller: Path, tests: Path) -> bool:
    """True for a module directly under ``tests/`` or anywhere beneath a tier folder."""
    if caller.parent == tests:
        return True
    try:
        relative = caller.relative_to(tests)
    except ValueError:
        return False
    return relative.parts[:1] != () and relative.parts[0] in _TIER_FOLDERS


def install_head_replay_guard() -> None:
    """Keep migration-chain replay in migration tests and test-support builders."""
    original = MigrationRunner.upgrade
    tests = repo_root() / "tests"

    def upgrade(self: MigrationRunner, revision: str = "head") -> None:
        # The module's own ``__file__``, not the code object's: a ``.pyc`` copied into mutants/ keeps the path it was compiled at.
        frame = inspect.currentframe()
        caller_frame = frame.f_back if frame else None
        caller = Path((caller_frame.f_globals.get("__file__") if caller_frame else None) or "").resolve()
        if (
            revision == "head"
            and _in_test_tree(caller, tests)
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
