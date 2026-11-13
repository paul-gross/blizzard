"""Offline store administration for the hub (``bzh:manual-migrations``).

The ``init`` / ``migrate`` verbs run while the daemon is **down** — the only carve-out
to "only a daemon opens its own store". Everything here is deterministic and store-only.

The module-level functions are this short-lived process's composition root
(``bzh:dependency-injection``): they wire in the system clock the revisions that stamp a time read.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from sqlalchemy import inspect, select
from sqlalchemy.exc import OperationalError

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.flags import FlagRegistry
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationConnectionError, MigrationRunner
from blizzard.hub.config import CONFIG_FILENAME, ConfigError, HubConfig, bootstrap_document
from blizzard.hub.config_keys import HUB_REGISTRY
from blizzard.hub.domain.observability.hub_events import HubEventLogService
from blizzard.hub.event_log_opener import system_event_log_opener
from blizzard.hub.secrets import hub_key_provider
from blizzard.hub.settings.footing import setting_keys
from blizzard.hub.store import MIGRATIONS_DIR, STORE_NAME
from blizzard.hub.store.schema import hub_setting_entries

MIGRATE_COMMAND = "blizzard hub migrate"

#: The first revision that creates ``hub_event_log``; a store ending below it has nowhere to record a move.
HUB_LOG_FLOOR_REVISION = "20261018_1200_project_color"

#: Opens the hub's own event log for a resolved config and closes it on exit; the engine URL is known only once the
#: config has loaded, so the seam is a factory.
HubEventLogOpener = Callable[[HubConfig], AbstractContextManager[HubEventLogService]]

#: Builds the migration runner for a resolved config, stamping revisions that read a time from the clock.
MigrationRunnerFactory = Callable[[HubConfig, IClock], MigrationRunner]

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


def _hub_migration_runner(config: HubConfig, clock: IClock) -> MigrationRunner:
    return Migrations(config, clock).runner


@dataclass(frozen=True)
class Runtime:
    """A hub runtime root, administered while the daemon is down."""

    root: Path
    clock: IClock
    #: Opens the hub event log ``init`` and ``migrate`` record ``hub-migrated`` in.
    open_event_log: HubEventLogOpener = field(kw_only=True)
    #: Builds the runner ``init`` and ``migrate`` move the store with.
    migration_runner: MigrationRunnerFactory = field(default=_hub_migration_runner, kw_only=True)
    allow_external_db: bool = False
    #: The registry every load of the root's config resolves its flags against.
    flag_registry: FlagRegistry = field(kw_only=True)
    #: Whether every load of the root's config reads it strictly.
    strict: bool = field(kw_only=True)

    def loaded(self, root: Path) -> HubConfig:
        path = root.resolve() / CONFIG_FILENAME
        if not path.exists():
            return HubConfig.load(
                root, allow_external_db=self.allow_external_db, flag_registry=self.flag_registry, strict=self.strict
            )
        document = HubConfig.read_document(root)
        environ = dict(os.environ)

        def load(document: dict[str, object]) -> HubConfig:
            return HubConfig.load(
                root,
                document=document,
                allow_external_db=self.allow_external_db,
                flag_registry=self.flag_registry,
                strict=self.strict,
                environ=environ,
            )

        bootstrap = load(bootstrap_document(document))
        engine = create_engine_from_url(bootstrap.db_url)
        try:
            with engine.connect() as conn:
                keys = (
                    tuple(conn.execute(select(hub_setting_entries.c.key)).scalars())
                    if inspect(conn).has_table(hub_setting_entries.name)
                    else ()
                )
        except OperationalError:
            # Let the migration runner report an unavailable store through its normal error seam.
            return load(document)
        finally:
            engine.dispose()
        remaining: dict[str, object] = deepcopy(document)
        live_keys = set(setting_keys(HUB_REGISTRY))
        for key in keys:
            if key not in live_keys:
                continue
            parts = key.split(".")
            section: dict[str, object] = remaining
            for part in parts[:-1]:
                child = section.get(part)
                if not isinstance(child, dict):
                    break
                section = cast(dict[str, object], child)
            else:
                section.pop(parts[-1], None)
        return load(remaining)

    def init(self) -> HubConfig:
        """Scaffold config + data dir + a migrated store + the secret key. Idempotent.

        Re-running leaves an existing config untouched and migrates the store to head, recording the move as
        ``hub-migrated`` when it changed the revision. A config whose db_url points outside the root is refused,
        not reconciled.
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

        runner = self.migration_runner(config, self.clock)
        self._move_store(config, runner, lambda: runner.upgrade("head"), refuse_as_config=True)
        _log.info("hub store migrated to head", root=str(root), db_url=config.db_url)
        hub_key_provider(os.environ, data_dir=config.data_dir)
        return config

    def migrate(self, *, down: str | None = None) -> None:
        """Apply pending revisions, or reverse to ``down`` when given. A move that changed the store's revision, in
        either direction, records one ``hub-migrated`` in the hub's own event log, naming the revisions it moved
        between; a store left below the hub log's own revision records nothing."""
        config = self.loaded(self.root)
        runner = self.migration_runner(config, self.clock)
        if down is not None:
            self._move_store(config, runner, lambda: runner.downgrade(down))
            _log.info("hub store downgraded", root=str(self.root), to=down)
        else:
            self._move_store(config, runner, lambda: runner.upgrade("head"))
            _log.info("hub store upgraded to head", root=str(self.root))

    def _move_store(
        self, config: HubConfig, runner: MigrationRunner, move: Callable[[], None], *, refuse_as_config: bool = False
    ) -> None:
        """Run ``move``, then record the revision change in the target revision's hub log, in a write of its own.

        With ``refuse_as_config``, a failure to open the store for the first revision read, or of the move itself to
        reach it, is a ``ConfigError``; anything after the move propagates as raised."""
        if not refuse_as_config:
            before = runner.current_revision()
            move()
        else:
            try:
                before = runner.current_revision()
            except OperationalError as exc:
                raise ConfigError(f"cannot open the hub store at {config.db_url}: {exc}") from exc
            try:
                move()
            except MigrationConnectionError as exc:
                raise ConfigError(f"the upgrade of the hub store at {config.db_url} failed: {exc}") from exc
        after = runner.current_revision()
        if after == before or not runner.reaches(after, HUB_LOG_FLOOR_REVISION):
            return
        with self.open_event_log(config) as event_log:
            event_log.record(
                kind="hub-migrated",
                message=f"hub store migrated from revision {before or 'none'} to {after}",
                detail={"from": before, "to": after},
                at=self.clock.now(),
            )


def migration_runner(config: HubConfig) -> MigrationRunner:
    return Migrations(config, SystemClock()).runner


def _runtime_of(root: Path, allow_external_db: bool, flag_registry: FlagRegistry, strict: bool) -> Runtime:
    clock = SystemClock()
    return Runtime(
        root,
        clock,
        allow_external_db,
        open_event_log=system_event_log_opener(clock),
        flag_registry=flag_registry,
        strict=strict,
    )


def init_environment(
    root: Path, *, allow_external_db: bool = False, flag_registry: FlagRegistry, strict: bool
) -> HubConfig:
    return _runtime_of(root, allow_external_db, flag_registry, strict).init()


def migrate(
    root: Path, *, down: str | None = None, allow_external_db: bool = False, flag_registry: FlagRegistry, strict: bool
) -> None:
    _runtime_of(root, allow_external_db, flag_registry, strict).migrate(down=down)


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
