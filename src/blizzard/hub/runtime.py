"""Offline store administration for the hub (``bzh:manual-migrations``).

The ``init`` / ``migrate`` verbs run while the daemon is **down** — the only carve-out
to "only a daemon opens its own store". Everything here is deterministic and store-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.config import CONFIG_FILENAME, HubConfig
from blizzard.hub.store import MIGRATIONS_DIR, STORE_NAME

MIGRATE_COMMAND = "blizzard hub migrate"

_log = get_logger("blizzard.hub.runtime")


@dataclass(frozen=True)
class Migrations:
    """The hub's Alembic tree, bound to one resolved config."""

    config: HubConfig

    @property
    def runner(self) -> MigrationRunner:
        return MigrationRunner(script_location=MIGRATIONS_DIR, url=self.config.db_url)

    def check_current(self) -> None:
        """Refuse to run on a store-revision mismatch, naming the migrate command."""
        self.runner.check_current(store=STORE_NAME, remedy=f"{MIGRATE_COMMAND} --dir {self.config.root}")


@dataclass(frozen=True)
class Runtime:
    """A hub runtime root, administered while the daemon is down."""

    root: Path
    allow_external_db: bool = False

    def loaded(self, root: Path) -> HubConfig:
        return HubConfig.load(root, allow_external_db=self.allow_external_db)

    def init(self) -> HubConfig:
        """Scaffold config + data dir + a migrated store. Idempotent.

        Re-running leaves an existing config untouched and migrates the store to head. A
        config whose db_url points outside the root is refused, not reconciled (issue #234).
        """
        root = self.root.resolve()
        root.mkdir(parents=True, exist_ok=True)

        config = HubConfig.scaffold(root)
        config.data_dir.mkdir(parents=True, exist_ok=True)
        if not config.config_path.exists():
            config.config_path.write_text(config.to_toml())
            _log.info("hub config scaffolded", path=str(config.config_path))
        else:
            config = self.loaded(root)

        Migrations(config).runner.upgrade("head")
        _log.info("hub store migrated to head", root=str(root), db_url=config.db_url)
        return config

    def migrate(self, *, down: str | None = None) -> None:
        """Apply pending revisions, or reverse to ``down`` when given."""
        runner = Migrations(self.loaded(self.root)).runner
        if down is not None:
            runner.downgrade(down)
            _log.info("hub store downgraded", root=str(self.root), to=down)
        else:
            runner.upgrade("head")
            _log.info("hub store upgraded to head", root=str(self.root))


def migration_runner(config: HubConfig) -> MigrationRunner:
    return Migrations(config).runner


def init_environment(root: Path, *, allow_external_db: bool = False) -> HubConfig:
    return Runtime(root, allow_external_db).init()


def migrate(root: Path, *, down: str | None = None, allow_external_db: bool = False) -> None:
    Runtime(root, allow_external_db).migrate(down=down)


def ensure_current_revision(config: HubConfig) -> None:
    Migrations(config).check_current()


__all__ = [
    "CONFIG_FILENAME",
    "MIGRATE_COMMAND",
    "HubConfig",
    "Migrations",
    "Runtime",
    "ensure_current_revision",
    "init_environment",
    "migrate",
    "migration_runner",
]
