"""Offline store administration for the hub (``bzh:manual-migrations``).

The ``init`` / ``migrate`` verbs run while the daemon is **down**: they are the
only carve-out to "only a daemon opens its own store". Everything here is
deterministic and store-only — no model calls, no server. ``init`` is idempotent;
``ensure_current_revision`` is the guard the daemon calls at startup to refuse to
run on a schema mismatch.
"""

from __future__ import annotations

from pathlib import Path

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.config import CONFIG_FILENAME, HubConfig
from blizzard.hub.store import MIGRATIONS_DIR, STORE_NAME

MIGRATE_COMMAND = "blizzard hub migrate"

_log = get_logger("blizzard.hub.runtime")


def migration_runner(config: HubConfig) -> MigrationRunner:
    return MigrationRunner(script_location=MIGRATIONS_DIR, url=config.db_url)


def init_environment(root: Path) -> HubConfig:
    """Scaffold config + data dir + a migrated store under ``root``. Idempotent.

    Re-running reconciles: an existing config file is left untouched, the data dir
    is ensured, and the store is migrated to head — a no-op when already current
    (the Alembic version table makes re-application safe).
    """
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    config = HubConfig.scaffold(root)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    if not config.config_path.exists():
        config.config_path.write_text(config.to_toml())
        _log.info("hub config scaffolded", path=str(config.config_path))
    else:
        config = HubConfig.load(root)

    migration_runner(config).upgrade("head")
    _log.info("hub store migrated to head", root=str(root), db_url=config.db_url)
    return config


def migrate(root: Path, *, down: str | None = None) -> None:
    """Apply pending revisions, or reverse to ``down`` when given."""
    config = HubConfig.load(root)
    runner = migration_runner(config)
    if down is not None:
        runner.downgrade(down)
        _log.info("hub store downgraded", root=str(root), to=down)
    else:
        runner.upgrade("head")
        _log.info("hub store upgraded to head", root=str(root))


def ensure_current_revision(config: HubConfig) -> None:
    """Refuse to run on a store-revision mismatch, naming the migrate command."""
    migration_runner(config).check_current(store=STORE_NAME, remedy=f"{MIGRATE_COMMAND} --dir {config.root}")


__all__ = [
    "CONFIG_FILENAME",
    "MIGRATE_COMMAND",
    "HubConfig",
    "ensure_current_revision",
    "init_environment",
    "migrate",
    "migration_runner",
]
