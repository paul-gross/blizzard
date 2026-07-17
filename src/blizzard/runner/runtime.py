"""Offline store administration for the runner (D-099, ``bzh:manual-migrations``).

The ``init`` / ``migrate`` verbs run while the daemon is **down** — the only
carve-out to "only a daemon opens its own store". Deterministic and
store-only: no model calls, no server. ``init`` is idempotent;
``ensure_current_revision`` is the guard the daemon calls at startup to refuse to
run on a schema mismatch.
"""

from __future__ import annotations

from pathlib import Path

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner.config import CONFIG_FILENAME, WORKER_SETTINGS_FILENAME, RunnerConfig
from blizzard.runner.harness.worker_settings import worker_settings_json
from blizzard.runner.store import MIGRATIONS_DIR, STORE_NAME

MIGRATE_COMMAND = "blizzard runner migrate"

_log = get_logger("blizzard.runner.runtime")


def migration_runner(config: RunnerConfig) -> MigrationRunner:
    return MigrationRunner(script_location=MIGRATIONS_DIR, url=config.db_url)


def init_environment(root: Path) -> RunnerConfig:
    """Scaffold config + data dir + a migrated store under ``root``. Idempotent.

    Re-running reconciles: an existing config file is left untouched, the data dir
    is ensured, and the store is migrated to head — a no-op when already current.
    """
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    config = RunnerConfig.scaffold(root)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    if not config.config_path.exists():
        config.config_path.write_text(config.to_toml())
        _log.info("runner config scaffolded", path=str(config.config_path))
    else:
        config = RunnerConfig.load(root)

    # The runner-owned worker hook file (heartbeat PostToolUse) the adapter delivers as
    # `--settings`. Written idempotently: the content is
    # versioned with the runner, so re-running `init` refreshes it to head.
    (root / WORKER_SETTINGS_FILENAME).write_text(worker_settings_json())

    migration_runner(config).upgrade("head")
    _log.info("runner store migrated to head", root=str(root), db_url=config.db_url)
    return config


def migrate(root: Path, *, down: str | None = None) -> None:
    """Apply pending revisions, or reverse to ``down`` when given."""
    config = RunnerConfig.load(root)
    runner = migration_runner(config)
    if down is not None:
        runner.downgrade(down)
        _log.info("runner store downgraded", root=str(root), to=down)
    else:
        runner.upgrade("head")
        _log.info("runner store upgraded to head", root=str(root))


def ensure_current_revision(config: RunnerConfig) -> None:
    """Refuse to run on a store-revision mismatch, naming the migrate command."""
    migration_runner(config).check_current(store=STORE_NAME, remedy=f"{MIGRATE_COMMAND} --dir {config.root}")


__all__ = [
    "CONFIG_FILENAME",
    "MIGRATE_COMMAND",
    "RunnerConfig",
    "ensure_current_revision",
    "init_environment",
    "migrate",
    "migration_runner",
]
