"""Offline store administration for the host directory (``bzh:manual-migrations``).

The ``init``/``migrate``/``store reset`` verbs run while the host is **down** — the only carve-out
to "only a daemon opens its own store". ``init`` is idempotent; ``Migrations.check_current``
is the startup guard that refuses to run on a schema mismatch. A store is never deleted: resetting
one moves its files aside under a timestamped name and migrates a fresh one in their place. Resetting one runner
extracts its rows into an archive store beside the host's, written whole before they are deleted.

A host directory has one owner. Every verb that moves or rewrites the store but a plain upgrade holds the directory's
lock for its write and refuses while the lock is held or a pre-host daemon answers on ``runner.sock``; a plain upgrade
refuses only the daemon, so the standard redeploy pass migrates beside a running host."""

from __future__ import annotations

import contextlib
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from blizzard.foundation.flags import FlagRegistry
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import collaborator, domain_model
from blizzard.foundation.store.migrations import MigrationConnectionError, MigrationRefused, MigrationRunner
from blizzard.runner.config_table import ConfigError
from blizzard.runner.harness.wiring import declared
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_file import write_scaffold
from blizzard.runner.host_layout import (
    LEGACY_CONFIG_FILENAME,
    LEGACY_STORE_FILENAME,
    MIGRATE_COMMAND,
    Graduation,
    HostHeld,
    HostLayout,
    HostLock,
    migrate_remedy,
)
from blizzard.runner.store import MIGRATIONS_DIR, STORE_NAME
from blizzard.runner.transfer.runner_stores import IRunnerBoundStoresFactory

STORE_RESET_COMMAND = "blizzard runner store reset"

#: The files beside a SQLite database that hold part of it, each moved with it.
_SQLITE_SIBLING_SUFFIXES = ("-wal", "-shm", "-journal")

_log = get_logger("blizzard.runner.runtime")


class IStoreLocation(Protocol):
    """Where a host's store is: its directory and the URL it is opened by."""

    @property
    def root(self) -> Path: ...
    @property
    def db_url(self) -> str: ...


@dataclass(frozen=True)
class Migrations:
    """The runner's Alembic tree, bound to one host's store."""

    location: IStoreLocation

    @property
    def runner(self) -> MigrationRunner:
        return MigrationRunner(script_location=MIGRATIONS_DIR, url=self.location.db_url)

    def check_current(self) -> None:
        """Refuse to run on a store-revision mismatch, naming the migrate command."""
        self.runner.check_current(store=STORE_NAME, remedy=migrate_remedy(self.location.root))


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


def named_store_socket(config: HostConfig) -> Path | None:
    """The pre-host daemon socket beside a store the host file's ``db_url`` names outside the host directory, where that
    store sits in a ``data`` directory as a pre-host runner keeps it (``<dir>/data/runner.db``, its socket
    ``<dir>/runner.sock``); ``None`` for a store in the host directory, a server database, or any other layout."""
    try:
        store = sqlite_store_file(config.db_url)
    except StoreResetRefused:
        return None
    if store.parent.parent == config.root or store.parent.name != "data":
        return None
    return store.parent.parent / config.layout.legacy_socket_path.name


@domain_model
@dataclass(frozen=True)
class SetAsideStore:
    """A runner store moved aside: each file that held it, under its stamped name."""

    moved: tuple[Path, ...]


def move_sqlite(database: Path, target: Path) -> tuple[Path, ...]:
    """Move ``database`` and its siblings to ``target`` and its siblings, never deleting any.

    The write-ahead log is folded into the database first, so the database moves in one rename and a
    sibling left behind by a crash between renames holds nothing a fresh store at the old name would
    replay. The database moves first: where ``target`` exists, the move happened. Raises
    :class:`StoreResetRefused` when a name at ``target`` is already taken."""
    present = [
        (database.with_name(database.name + suffix), target.with_name(target.name + suffix))
        for suffix in ("", *_SQLITE_SIBLING_SUFFIXES)
        if database.with_name(database.name + suffix).exists()
    ]
    taken = [destination for _, destination in present if destination.exists()]
    if taken:
        raise StoreResetRefused(f"cannot move the runner store: {taken[0]} already exists")
    if database.exists():
        with contextlib.closing(sqlite3.connect(database)) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    moved: list[Path] = []
    for source, destination in present:
        if source.exists():
            source.rename(destination)
            moved.append(destination)
    return tuple(moved)


def set_aside_sqlite(database: Path, *, now: datetime) -> SetAsideStore:
    """Move ``database`` and its siblings aside under ``<name>.<UTC stamp>``, never deleting any."""
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return SetAsideStore(moved=move_sqlite(database, database.with_name(f"{database.name}.{stamp}")))


def sqlite_url(database: Path) -> str:
    """The store URL that opens the SQLite file ``database``."""
    return f"sqlite:///{database.resolve()}"


def single_file(database: Path) -> None:
    """Fold ``database``'s write-ahead log into it and leave it in rollback-journal mode, so the file alone holds the
    whole store and moves in one rename."""
    with contextlib.closing(sqlite3.connect(database)) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")


def backup_sqlite(database: Path, copy: Path) -> None:
    """Copy the SQLite store ``database`` into ``copy`` through SQLite's backup API, read over a read-only connection:
    the copy holds every committed transaction, its write-ahead log's among them, and neither the database nor its log
    is written — a read-only connection runs no checkpoint."""
    with (
        contextlib.closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as source,
        contextlib.closing(sqlite3.connect(copy)) as destination,
    ):
        source.backup(destination)


@domain_model
@dataclass(frozen=True)
class RunnerArchive:
    """One runner's rows extracted from the host store: the runner, and the archive store that now holds them."""

    runner_id: str
    archive: Path


def _holds_no_tables(database: Path) -> bool:
    """Whether the SQLite file at ``database`` exists and holds no table — a file a connect created, not a store."""
    if not database.exists():
        return False
    with contextlib.closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        return conn.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] == 0


def _nothing_answers(_socket: Path) -> str | None:
    return None


@collaborator
@dataclass(frozen=True)
class Runtime:
    """A host directory, administered while the host is down."""

    root: Path
    #: The hub a fresh config names, over ``BZ_HUB_URL``; an existing config naming another is refused.
    hub_url: str | None = None
    #: The projects a fresh config's runner serves, over the scaffold's ``default``; an existing config naming
    #: others is refused.
    projects: tuple[str, ...] | None = None
    #: What holds a socket path, if anything — the probe for a pre-host daemon on ``runner.sock``.
    holder: Callable[[Path], str | None] = _nothing_answers
    #: The registry every load of the host file resolves its flags against.
    flag_registry: FlagRegistry = field(kw_only=True)
    #: Whether every load of the host file reads it strictly.
    strict: bool = field(kw_only=True)

    def init(self) -> HostConfig:
        """Scaffold the host file + data dir + a migrated store. Idempotent.

        Re-running reconciles: an existing host file is left untouched, the data dir is ensured, and the store is
        migrated to head — a no-op when already current. A directory holding no host file and a pre-host store is
        refused, naming graduation; :meth:`set_aside_pre_host_store` clears the way."""
        root = self.root.resolve()
        layout = HostLayout(root)
        try:
            root.mkdir(parents=True, exist_ok=True)
            if layout.graduation() is Graduation.UNINITIALISED:
                if layout.legacy_store_path.exists():
                    raise ConfigError(
                        f"{root} holds {layout.legacy_store_path.relative_to(root)} from a runner before hosts, "
                        f"and no {LEGACY_CONFIG_FILENAME} beside it: follow the graduation procedure in "
                        "docs/upgrade.md, or re-run with --allow-readd to set that store aside and start a host "
                        "in its place"
                    )
                scaffold = HostConfig.scaffold(root, hub_url=self.hub_url, projects=self.projects)
                layout.data_dir.mkdir(parents=True, exist_ok=True)
                with contextlib.suppress(FileExistsError):
                    write_scaffold(layout, scaffold.to_toml())
                    _log.info("host config scaffolded", path=str(layout.config_path))
            config = HostConfig.load(root, flag_registry=self.flag_registry, strict=self.strict)
            self._agree_with_arguments(config)
            layout.data_dir.mkdir(parents=True, exist_ok=True)
            # Every binding's runner-owned files, versioned with the runner, so re-running
            # `init` refreshes them to head.
            for entry in config.runners:
                if entry.own is not None:
                    for declaration, section in declared(entry.own.harness_sections):
                        declaration.scaffold_runtime(section, root)
        except OSError as exc:
            raise ConfigError(f"cannot write the host directory at {root}: {exc}") from exc
        self._upgrade(config)
        _log.info("runner store migrated to head", root=str(root), db_url=config.db_url)
        return config

    def set_aside_pre_host_store(self, *, now: datetime) -> SetAsideStore | None:
        """The store a pre-host runner left in a directory that holds no host file — a provisioning step cleared
        ``blizzard-runner.toml`` — moved aside, for a host to start in its place. Nothing in a directory that holds
        either file or no pre-host store is touched."""
        layout = HostLayout(self.root.resolve())
        if layout.graduation() is not Graduation.UNINITIALISED or not layout.legacy_store_path.exists():
            return None
        with self._exclusive(layout, f"set the pre-host {LEGACY_STORE_FILENAME} aside"):
            return set_aside_sqlite(layout.legacy_store_path, now=now)

    def reset_store(self, *, now: datetime) -> SetAsideStore:
        """Move the store aside under a ``now``-stamped name and migrate a fresh one to head in its
        place. Never deletes. Refused while the directory is held."""
        config = HostConfig.load(self.root, flag_registry=self.flag_registry, strict=self.strict)
        config.refuse_unadopted()
        with self._exclusive(config.layout, "move the runner store aside"):
            moved = set_aside_sqlite(sqlite_store_file(config.db_url), now=now)
            self._migrate_to_head(config)
        _log.info("runner store set aside", root=str(self.root), moved=[str(p) for p in moved.moved])
        return moved

    def reset_runner(self, runner: str, *, now: datetime, rows: IRunnerBoundStoresFactory) -> RunnerArchive:
        """Extract the runner ``runner`` names — by name or id, or a runner the host file no longer configures by its
        id — into an archive store beside the host's, named by the runner and a ``now`` stamp, then delete its rows
        from the host store, keeping its outbox counters. The archive is migrated to head and written whole under a
        temporary name, then renamed, before anything is deleted. Refused while the directory is held."""
        config = HostConfig.load(self.root, flag_registry=self.flag_registry, strict=self.strict)
        config.refuse_unadopted()
        store = sqlite_store_file(config.db_url)
        runner_id, label = _reset_target(config, runner)
        stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        archive = store.with_name(f"{store.name}.{label}.{stamp}")
        with self._exclusive(config.layout, f"reset runner {label}"):
            if archive.exists():
                raise StoreResetRefused(f"cannot archive runner {label}: {archive} already exists")
            pending = archive.with_name(archive.name + ".tmp")
            for leftover in (pending, *(pending.with_name(pending.name + s) for s in _SQLITE_SIBLING_SUFFIXES)):
                leftover.unlink(missing_ok=True)
            try:
                MigrationRunner(script_location=MIGRATIONS_DIR, url=sqlite_url(pending)).upgrade("head")
            except (MigrationConnectionError, MigrationRefused) as exc:
                raise ConfigError(f"cannot create the archive store at {pending}: {exc}") from exc
            moving = rows.for_runner(runner_id)
            moving.copy_runner(
                config.db_url, sqlite_url(pending), bound_workspaces=_bound_workspaces(config, runner_id)
            )
            single_file(pending)
            os.replace(pending, archive)
            moving.remove_runner_rows(config.db_url, keep_counters=True)
        _log.info("runner reset", root=str(self.root), runner_id=runner_id, archive=str(archive))
        return RunnerArchive(runner_id=runner_id, archive=archive)

    def migrate(self, *, down: str | None = None) -> None:
        """Apply pending revisions — adopting a pre-host store first, when the directory holds one and no host
        store — or reverse to ``down`` when given."""
        config = HostConfig.load(self.root, flag_registry=self.flag_registry, strict=self.strict)
        if down is not None:
            config.refuse_unadopted()
            with self._exclusive(config.layout, "reverse the store's migrations"):
                self._downgrade(config, down)
            _log.info("runner store downgraded", root=str(self.root), to=down)
            return
        self._upgrade(config)
        _log.info("runner store upgraded to head", root=str(self.root))

    def _agree_with_arguments(self, config: HostConfig) -> None:
        """Refuse an existing host file that names another hub or other projects than the ones this init was given."""
        if self.hub_url is not None and config.hub_url.rstrip("/") != self.hub_url.rstrip("/"):
            raise ConfigError(f"{config.layout.config_path} names the hub {config.hub_url}, not {self.hub_url}")
        if self.projects is not None:
            held = tuple(binding.project for binding in config.runners[0].bindings) if config.runners else ()
            if held != self.projects:
                raise ConfigError(
                    f"{config.layout.config_path} holds projects = {list(held)}, not the projects "
                    f"{list(self.projects)}; edit its `projects`"
                )

    def _upgrade(self, config: HostConfig) -> None:
        """Migrate the store to head, adopting the pre-host store first when the default store is absent and that one
        is not. A default store that holds tables beside the pre-host one is refused: a crash mid-adoption cannot leave
        both, since :func:`move_sqlite` renames the database in one step and the pre-host name is then gone. Only a
        plain upgrade over an existing store runs beside a host; it refuses the daemon alone."""
        layout = config.layout
        adopting = (
            config.db_url == layout.default_db_url
            and not layout.store_path.exists()
            and layout.legacy_store_path.exists()
        )
        if not adopting:
            if (
                config.db_url == layout.default_db_url
                and layout.legacy_store_path.exists()
                and layout.store_path.exists()
                and not _holds_no_tables(layout.store_path)
            ):
                raise ConfigError(
                    f"{layout.store_path} holds tables beside the pre-host store {layout.legacy_store_path}; an "
                    f"operator has to decide which one is the runner's store, and move the other aside, before "
                    f"`{migrate_remedy(layout.root)}` runs (nothing was changed)"
                )
            if (
                config.db_url == layout.default_db_url
                and layout.legacy_store_path.exists()
                and _holds_no_tables(layout.store_path)
            ):
                raise ConfigError(
                    f"{layout.store_path} is an empty file beside the pre-host store {layout.legacy_store_path}, so "
                    f"neither is adopted; if {layout.store_path.name} is a stray left by a verb that opened it, "
                    f"remove it and re-run `{migrate_remedy(layout.root)}`"
                )
            if (held := self.holder(layout.legacy_socket_path)) is not None:
                raise ConfigError(f"{held}, so this cannot migrate the store — stop it first, then re-run")
            if (named := named_store_socket(config)) is not None and (held := self.holder(named)) is not None:
                raise ConfigError(
                    f"{held}, and it holds the store {config.db_url} the host file names, so this cannot migrate "
                    "it — stop it first, or drop the `db_url` so the host's own store applies"
                )
            self._migrate_to_head(config)
            return
        with self._exclusive(layout, f"adopt {layout.legacy_store_path} as the host store"):
            if not layout.store_path.exists():
                legacy = MigrationRunner(script_location=MIGRATIONS_DIR, url=sqlite_url(layout.legacy_store_path))
                legacy.refuse_below_floor(store=str(layout.legacy_store_path))
                move_sqlite(layout.legacy_store_path, layout.store_path)
                _log.info("pre-host store adopted", store=str(layout.store_path))
            self._migrate_to_head(config)

    def _migrate_to_head(self, config: HostConfig) -> None:
        try:
            Migrations(config).runner.upgrade("head")
        except MigrationConnectionError as exc:
            raise ConfigError(f"cannot open the runner store at {config.db_url}: {exc}") from exc
        except MigrationRefused as exc:
            raise ConfigError(f"the runner store at {config.db_url} cannot be migrated: {exc}") from exc

    def _downgrade(self, config: HostConfig, revision: str) -> None:
        try:
            Migrations(config).runner.downgrade(revision)
        except MigrationRefused as exc:
            raise ConfigError(f"the runner store at {config.db_url} cannot be migrated: {exc}") from exc

    @contextmanager
    def _exclusive(self, layout: HostLayout, action: str) -> Iterator[None]:
        """The host directory held for a write: its lock taken, and no pre-host daemon answering on ``runner.sock``."""
        try:
            lock = HostLock.acquire(layout)
        except HostHeld as exc:
            raise ConfigError(f"{exc}, so this cannot {action} — stop it first, then re-run") from exc
        with lock:
            if (held := self.holder(layout.legacy_socket_path)) is not None:
                raise ConfigError(f"{held}, so this cannot {action} — stop it first, then re-run")
            yield


def _reset_target(config: HostConfig, runner: str) -> tuple[str, str]:
    """The id of the runner ``runner`` names and the label its archive is named by: a configured runner by its name or
    id, labelled by its name, or a runner the host file no longer configures by its ``rn_`` id."""
    for entry in config.runners:
        if runner in (entry.name, entry.id):
            if entry.id is None:
                raise ConfigError(
                    f"runner {entry.name} has no id in {config.config_path}, so the store holds no rows of it"
                )
            return entry.id, entry.name
    if runner.startswith(_RUNNER_ID_PREFIX):
        return runner, runner
    held = ", ".join(entry.name for entry in config.runners) or "none"
    raise ConfigError(
        f"{config.config_path} declares no runner {runner!r} (it holds {held}); name a runner it declares, or one it "
        "no longer declares by its id"
    )


def _bound_workspaces(config: HostConfig, runner_id: str) -> tuple[str, ...]:
    """The workspaces the host file's ``[[runner]]`` for ``runner_id`` binds; none for one it no longer declares."""
    for entry in config.runners:
        if entry.id == runner_id:
            return tuple(dict.fromkeys(binding.workspace for binding in entry.bindings))
    return ()


#: The prefix every hub-minted runner id carries.
_RUNNER_ID_PREFIX = "rn_"


def migration_runner(location: IStoreLocation) -> MigrationRunner:
    return Migrations(location).runner


def init_environment(
    root: Path,
    *,
    hub_url: str | None = None,
    projects: tuple[str, ...] | None = None,
    holder: Callable[[Path], str | None] = _nothing_answers,
    flag_registry: FlagRegistry,
    strict: bool,
) -> HostConfig:
    return Runtime(
        root, hub_url=hub_url, projects=projects, holder=holder, flag_registry=flag_registry, strict=strict
    ).init()


def migrate(
    root: Path,
    *,
    down: str | None = None,
    holder: Callable[[Path], str | None] = _nothing_answers,
    flag_registry: FlagRegistry,
    strict: bool,
) -> None:
    Runtime(root, holder=holder, flag_registry=flag_registry, strict=strict).migrate(down=down)


def ensure_current_revision(location: IStoreLocation) -> None:
    Migrations(location).check_current()


__all__ = [
    "MIGRATE_COMMAND",
    "STORE_RESET_COMMAND",
    "Migrations",
    "RunnerArchive",
    "Runtime",
    "SetAsideStore",
    "StoreResetRefused",
    "backup_sqlite",
    "ensure_current_revision",
    "init_environment",
    "migrate",
    "migration_runner",
    "move_sqlite",
    "reset_command",
    "set_aside_sqlite",
    "single_file",
    "sqlite_store_file",
    "sqlite_url",
]
