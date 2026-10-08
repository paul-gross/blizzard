"""Offline store administration for the hub (``bzh:manual-migrations``).

The ``init`` / ``migrate`` verbs run while the daemon is **down** — the only carve-out
to "only a daemon opens its own store". Everything here is deterministic and store-only.

The module-level functions are this short-lived process's composition root
(``bzh:dependency-injection``): they wire in the system clock the revisions that stamp a time read.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.migrations import MigrationConnectionError, MigrationRunner
from blizzard.hub.config import CONFIG_FILENAME, ConfigError, HubConfig
from blizzard.hub.secrets import hub_key_provider
from blizzard.hub.store import MIGRATIONS_DIR, STORE_NAME

MIGRATE_COMMAND = "blizzard hub migrate"

_log = get_logger("blizzard.hub.runtime")


@dataclass(frozen=True)
class Migrations:
    """The hub's Alembic tree, bound to one resolved config. ``clock`` is what a revision that
    stamps a time reads; checking the store's revision needs none."""

    config: HubConfig
    clock: IClock | None = None

    @property
    def runner(self) -> MigrationRunner:
        return MigrationRunner(script_location=MIGRATIONS_DIR, url=self.config.db_url, clock=self.clock)

    def check_current(self) -> None:
        """Refuse to run on a store-revision mismatch, naming the migrate command."""
        self.runner.check_current(store=STORE_NAME, remedy=f"{MIGRATE_COMMAND} --dir {self.config.root}")


@dataclass(frozen=True)
class Runtime:
    """A hub runtime root, administered while the daemon is down."""

    root: Path
    clock: IClock
    allow_external_db: bool = False

    def loaded(self, root: Path) -> HubConfig:
        return HubConfig.load(root, allow_external_db=self.allow_external_db)

    def init(self) -> HubConfig:
        """Scaffold config + data dir + a migrated store + the secret key. Idempotent.

        Re-running leaves an existing config untouched and migrates the store to head. A
        config whose db_url points outside the root is refused, not reconciled.
        """
        root = self.root.resolve()
        try:
            root.mkdir(parents=True, exist_ok=True)

            config = HubConfig.scaffold(root)
            config.data_dir.mkdir(parents=True, exist_ok=True)
            if not config.config_path.exists():
                config.config_path.write_text(config.to_toml())
                _log.info("hub config scaffolded", path=str(config.config_path))
            else:
                config = self.loaded(root)
        except OSError as exc:
            raise ConfigError(f"cannot write the hub runtime at {root}: {exc}") from exc

        try:
            Migrations(config, self.clock).runner.upgrade("head")
        except MigrationConnectionError as exc:
            raise ConfigError(f"cannot open the hub store at {config.db_url}: {exc}") from exc
        _log.info("hub store migrated to head", root=str(root), db_url=config.db_url)
        hub_key_provider(os.environ, data_dir=config.data_dir)
        return config

    def migrate(self, *, down: str | None = None) -> None:
        """Apply pending revisions, or reverse to ``down`` when given."""
        runner = Migrations(self.loaded(self.root), self.clock).runner
        if down is not None:
            runner.downgrade(down)
            _log.info("hub store downgraded", root=str(self.root), to=down)
        else:
            runner.upgrade("head")
            _log.info("hub store upgraded to head", root=str(self.root))


def migration_runner(config: HubConfig) -> MigrationRunner:
    return Migrations(config, SystemClock()).runner


def init_environment(root: Path, *, allow_external_db: bool = False) -> HubConfig:
    return Runtime(root, SystemClock(), allow_external_db).init()


def migrate(root: Path, *, down: str | None = None, allow_external_db: bool = False) -> None:
    Runtime(root, SystemClock(), allow_external_db).migrate(down=down)


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
