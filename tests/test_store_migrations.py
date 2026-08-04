"""Store-migration behaviour for both daemon trees (unit tier).

Covers the three guarantees the scaffold owes the migration policy
(``bzh:manual-migrations``): ``init`` is idempotent, ``migrate`` goes up
and down, and a daemon refuses to start on a revision mismatch — naming its exact
migrate command.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import RevisionMismatchError
from blizzard.hub import runtime as hub_runtime
from tests.conftest import Daemon

pytestmark = pytest.mark.unit


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


def test_wrapped_takeover_command_column_survives_migration_roundtrip(tmp_path: Path) -> None:
    """Hub-only (``escalations.wrapped_takeover_command`` has no runner counterpart).

    ``base -> head`` alone would exercise this revision's ``add_column`` branch
    vacuously — the walking-skeleton revision already creates ``escalations`` with
    every column current ``schema.py`` declares — so the middle downgrade below is
    what actually proves this revision's ``upgrade()``/``downgrade()`` bodies, not
    just its presence in the ladder. (``test_migrate_up_and_down[hub]`` already
    separately proves the ``_has_column`` guard's *skip* branch, via its own
    ``base -> head`` round trip landing the column exactly once either way — this
    test is the complementary case: the guard's *acting* branch, isolated to this
    one revision.)

    The downgrade target is this revision's own parent by id
    (``20260801_1600_hub_runner_external_usage``), not ``"-1"`` — pinned to the
    migration by name rather than to wherever it happens to sit in the ladder, so a
    future revision landing between this one and its parent can't silently break
    this test's premise.
    """
    config = hub_runtime.init_environment(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)

    def _has_column() -> bool:
        engine = create_engine_from_url(config.db_url)
        try:
            columns = {c["name"] for c in sa.inspect(engine).get_columns("escalations")}
        finally:
            engine.dispose()
        return "wrapped_takeover_command" in columns

    assert _has_column()

    runner.downgrade("20260801_1600_hub_runner_external_usage")
    assert not _has_column()

    runner.upgrade("head")
    assert _has_column()
