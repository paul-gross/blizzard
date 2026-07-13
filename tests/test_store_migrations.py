"""Store-migration behaviour for both daemon trees (unit tier).

Covers the three guarantees the scaffold owes the migration policy
(``bzh:manual-migrations``, D-099): ``init`` is idempotent, ``migrate`` goes up
and down, and a daemon refuses to start on a revision mismatch — naming its exact
migrate command.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.foundation.store.migrations import RevisionMismatchError
from tests.conftest import Daemon


def test_init_creates_config_data_dir_and_migrates_to_head(daemon: Daemon, tmp_path: Path) -> None:
    config = daemon.runtime.init_environment(tmp_path)

    assert config.config_path.exists()
    assert config.data_dir.is_dir()
    runner = daemon.runtime.migration_runner(config)
    assert runner.current_revision() == runner.script_head()
    assert runner.is_current()


def test_init_is_idempotent(daemon: Daemon, tmp_path: Path) -> None:
    first = daemon.runtime.init_environment(tmp_path)
    written = first.config_path.read_text()

    # Re-running reconciles and no-ops: no error, config untouched, still at head.
    second = daemon.runtime.init_environment(tmp_path)

    assert second.config_path.read_text() == written
    assert daemon.runtime.migration_runner(second).is_current()


def test_migrate_up_and_down(daemon: Daemon, tmp_path: Path) -> None:
    config = daemon.runtime.init_environment(tmp_path)
    runner = daemon.runtime.migration_runner(config)
    head = runner.script_head()
    assert head is not None
    assert runner.current_revision() == head

    daemon.runtime.migrate(tmp_path, down="base")
    assert runner.current_revision() is None

    daemon.runtime.migrate(tmp_path)
    assert runner.current_revision() == head


def test_daemon_refuses_on_revision_mismatch(daemon: Daemon, tmp_path: Path) -> None:
    config = daemon.runtime.init_environment(tmp_path)
    # Roll the store behind the code's head to simulate a version skew.
    daemon.runtime.migration_runner(config).downgrade("base")

    with pytest.raises(RevisionMismatchError) as excinfo:
        daemon.runtime.ensure_current_revision(config)

    message = str(excinfo.value)
    assert daemon.runtime.MIGRATE_COMMAND in message
    assert daemon.name in message


def test_ensure_current_revision_passes_at_head(daemon: Daemon, tmp_path: Path) -> None:
    config = daemon.runtime.init_environment(tmp_path)
    # No exception when the store is migrated exactly to head.
    daemon.runtime.ensure_current_revision(config)
