"""Offline store administration for the runner (``bzh:manual-migrations``).

The ``init``/``migrate``/``store reset`` verbs run while the daemon is **down** — the only carve-out
to "only a daemon opens its own store". ``init`` is idempotent; ``Migrations.check_current``
is the startup guard that refuses to run on a schema mismatch. A store is never deleted: resetting
one moves its files aside under a timestamped name and migrates a fresh one in their place."""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import collaborator, domain_model
from blizzard.foundation.store.migrations import MigrationConnectionError, MigrationRunner
from blizzard.runner.config import CONFIG_FILENAME, ConfigError, RunnerConfig
from blizzard.runner.harness.wiring import declared
from blizzard.runner.store import MIGRATIONS_DIR, STORE_NAME

MIGRATE_COMMAND = "blizzard runner migrate"
STORE_RESET_COMMAND = "blizzard runner store reset"

#: The files beside a SQLite database that hold part of it, each moved aside under the database's new name.
_SQLITE_SIBLING_SUFFIXES = ("-wal", "-shm", "-journal")

_log = get_logger("blizzard.runner.runtime")


@domain_model
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


class StoreResetRefused(Exception):
    """The store cannot be moved aside — it is not a SQLite file, or its stamped name is taken."""


def reset_command(root: Path) -> str:
    """The command that moves ``root``'s store aside and starts a fresh one."""
    return f"{STORE_RESET_COMMAND} {root}"


def sqlite_store_file(db_url: str) -> Path:
    """The database file a ``sqlite:///<path>`` store URL names. Raises :class:`StoreResetRefused`
    for any other store — a server database or an in-memory one has no file to move aside."""
    scheme, sep, rest = db_url.partition("://")
    location = rest.split("?", 1)[0].removeprefix("/")
    if not sep or scheme.split("+", 1)[0] != "sqlite" or location in ("", ":memory:"):
        raise StoreResetRefused(
            f"the runner store at {db_url} is not a SQLite file, so it cannot be moved aside; "
            "clear it with that database's own tools"
        )
    return Path(location)


@domain_model
@dataclass(frozen=True)
class SetAsideStore:
    """A runner store moved aside: each file that held it, under its stamped name."""

    moved: tuple[Path, ...]


def set_aside_sqlite(database: Path, *, now: datetime) -> SetAsideStore:
    """Move ``database`` and its siblings aside under ``<name>.<UTC stamp>``, never deleting any.

    The write-ahead log is folded into the database first, so the database moves in one rename and a
    sibling left behind by a crash between renames holds nothing a fresh store at the old name would
    replay. Raises :class:`StoreResetRefused` when a stamped name is already taken."""
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    aside = database.with_name(f"{database.name}.{stamp}")
    present = [
        (database.with_name(database.name + suffix), aside.with_name(aside.name + suffix))
        for suffix in ("", *_SQLITE_SIBLING_SUFFIXES)
        if database.with_name(database.name + suffix).exists()
    ]
    taken = [target for _, target in present if target.exists()]
    if taken:
        raise StoreResetRefused(f"cannot move the runner store aside: {taken[0]} already exists")
    if database.exists():
        with contextlib.closing(sqlite3.connect(database)) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    moved: list[Path] = []
    for source, target in present:
        if source.exists():
            source.rename(target)
            moved.append(target)
    return SetAsideStore(moved=tuple(moved))


@collaborator
@dataclass(frozen=True)
class Runtime:
    """A runner runtime root, administered while the daemon is down."""

    root: Path
    #: The hub a fresh config names, over ``BZ_HUB_URL``; an existing config naming another is refused.
    hub_url: str | None = None

    def init(self) -> RunnerConfig:
        """Scaffold config + data dir + a migrated store. Idempotent.

        Re-running reconciles: an existing config file is left untouched, the data dir is
        ensured, and the store is migrated to head — a no-op when already current.
        """
        root = self.root.resolve()
        try:
            root.mkdir(parents=True, exist_ok=True)

            # An existing file is authoritative and the environment is discarded, so `scaffold` — which
            # reads the environment and rejects a malformed value — must not run on that path.
            scaffolding = not (root / CONFIG_FILENAME).exists()
            config = RunnerConfig.scaffold(root, hub_url=self.hub_url) if scaffolding else RunnerConfig.load(root)
            if self.hub_url is not None and config.hub_url.rstrip("/") != self.hub_url.rstrip("/"):
                raise ConfigError(f"{config.config_path} names the hub {config.hub_url}, not {self.hub_url}")
            config.data_dir.mkdir(parents=True, exist_ok=True)
            if scaffolding:
                config.config_path.write_text(config.to_toml())
                _log.info("runner config scaffolded", path=str(config.config_path))

            # Every binding's runner-owned files, versioned with the runner, so re-running
            # `init` refreshes them to head.
            for declaration, section in declared(config.harness_sections):
                declaration.scaffold_runtime(section, root)
        except OSError as exc:
            raise ConfigError(f"cannot write the runner runtime at {root}: {exc}") from exc

        try:
            Migrations(config).runner.upgrade("head")
        except MigrationConnectionError as exc:
            raise ConfigError(f"cannot open the runner store at {config.db_url}: {exc}") from exc
        _log.info("runner store migrated to head", root=str(root), db_url=config.db_url)
        return config

    def reset_store(self, *, now: datetime) -> SetAsideStore:
        """Move the store aside under a ``now``-stamped name and migrate a fresh one to head in its
        place. Never deletes. The daemon must be down: its caller refuses while one answers."""
        config = RunnerConfig.load(self.root)
        moved = set_aside_sqlite(sqlite_store_file(config.db_url), now=now)
        try:
            Migrations(config).runner.upgrade("head")
        except MigrationConnectionError as exc:
            raise ConfigError(f"cannot open the runner store at {config.db_url}: {exc}") from exc
        _log.info("runner store set aside", root=str(self.root), moved=[str(p) for p in moved.moved])
        return moved

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


def init_environment(root: Path, *, hub_url: str | None = None) -> RunnerConfig:
    return Runtime(root, hub_url=hub_url).init()


def migrate(root: Path, *, down: str | None = None) -> None:
    Runtime(root).migrate(down=down)


def ensure_current_revision(config: RunnerConfig) -> None:
    Migrations(config).check_current()


__all__ = [
    "CONFIG_FILENAME",
    "MIGRATE_COMMAND",
    "STORE_RESET_COMMAND",
    "Migrations",
    "RunnerConfig",
    "Runtime",
    "SetAsideStore",
    "StoreResetRefused",
    "ensure_current_revision",
    "init_environment",
    "migrate",
    "migration_runner",
    "reset_command",
    "set_aside_sqlite",
    "sqlite_store_file",
]
