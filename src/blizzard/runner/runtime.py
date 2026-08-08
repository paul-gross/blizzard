"""Offline store administration for the runner (``bzh:manual-migrations``).

The ``init``/``migrate`` verbs run while the daemon is **down** — the only carve-out to
"only a daemon opens its own store". ``init`` is idempotent; ``Migrations.check_current``
is the startup guard that refuses to run on a schema mismatch."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.exc import OperationalError

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner.config import CONFIG_FILENAME, WORKER_SETTINGS_FILENAME, ConfigError, RunnerConfig
from blizzard.runner.harness.worker_settings import WorkerSettings
from blizzard.runner.store import MIGRATIONS_DIR, STORE_NAME

MIGRATE_COMMAND = "blizzard runner migrate"

_log = get_logger("blizzard.runner.runtime")


@dataclass(frozen=True)
class Migrations:
    """The runner's Alembic tree, bound to one resolved config."""

    config: RunnerConfig

    @property
    def runner(self) -> MigrationRunner:
        return MigrationRunner(script_location=MIGRATIONS_DIR, url=self.config.db_url)

    def check_current(self) -> None:
        """Refuse to run on a store-revision mismatch, naming the migrate command."""
        self.runner.check_current(store=STORE_NAME, remedy=f"{MIGRATE_COMMAND} --dir {self.config.root}")


@dataclass(frozen=True)
class Runtime:
    """A runner runtime root, administered while the daemon is down."""

    root: Path

    def init(self) -> RunnerConfig:
        """Scaffold config + data dir + a migrated store. Idempotent.

        Re-running reconciles: an existing config file is left untouched, the data dir is
        ensured, and the store is migrated to head — a no-op when already current.
        """
        root = self.root.resolve()
        try:
            root.mkdir(parents=True, exist_ok=True)

            # An existing file is authoritative and the environment is discarded, so `scaffold` — which
            # reads the environment and rejects a malformed value — must not run on that path (issue #287).
            scaffolding = not (root / CONFIG_FILENAME).exists()
            config = RunnerConfig.scaffold(root) if scaffolding else RunnerConfig.load(root)
            config.data_dir.mkdir(parents=True, exist_ok=True)
            if scaffolding:
                config.config_path.write_text(config.to_toml())
                _log.info("runner config scaffolded", path=str(config.config_path))

            # Written idempotently: the content is versioned with the runner, so re-running
            # `init` refreshes it to head.
            (root / WORKER_SETTINGS_FILENAME).write_text(WorkerSettings.of().json)
        except OSError as exc:
            raise ConfigError(f"cannot write the runner runtime at {root}: {exc}") from exc

        try:
            Migrations(config).runner.upgrade("head")
        except OperationalError as exc:
            raise ConfigError(f"cannot open the runner store at {config.db_url}: {exc}") from exc
        _log.info("runner store migrated to head", root=str(root), db_url=config.db_url)
        return config

    def migrate(self, *, down: str | None = None) -> None:
        """Apply pending revisions, or reverse to ``down`` when given."""
        runner = Migrations(RunnerConfig.load(self.root)).runner
        if down is not None:
            runner.downgrade(down)
            _log.info("runner store downgraded", root=str(self.root), to=down)
        else:
            runner.upgrade("head")
            _log.info("runner store upgraded to head", root=str(self.root))


def migration_runner(config: RunnerConfig) -> MigrationRunner:
    return Migrations(config).runner


def init_environment(root: Path) -> RunnerConfig:
    return Runtime(root).init()


def migrate(root: Path, *, down: str | None = None) -> None:
    Runtime(root).migrate(down=down)


def ensure_current_revision(config: RunnerConfig) -> None:
    Migrations(config).check_current()


__all__ = [
    "CONFIG_FILENAME",
    "MIGRATE_COMMAND",
    "Migrations",
    "RunnerConfig",
    "Runtime",
    "ensure_current_revision",
    "init_environment",
    "migrate",
    "migration_runner",
]
