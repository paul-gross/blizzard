"""The Alembic migration runner and the revision-mismatch guard.

Schema change is Alembic (``bzh:manual-migrations``). The runner drives *one* migration tree, pointed at a
``script_location`` and a store ``url``; the guard refuses to run on a revision mismatch. A revision that stamps a
time reads it from the clock the runner was given (``migration_clock``), never the wall clock
(``bzh:injected-clock``)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from alembic import command, op
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.script.revision import ResolutionError
from alembic.util import CommandError
from sqlalchemy import Column, MetaData, String, Table, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url

# Alembic's default `version_num String(32)` truncates `YYYYMMDD_HHMM_slug` ids on postgres (#191).
# Pinned by tests/test_pin_foundation.py::test_the_version_table_admits_this_projects_revision_ids.
_VERSION_TABLE_COLUMN_LENGTH = 255

# The Alembic ``Config.attributes`` key a runner's clock rides under, for ``migration_clock`` to read.
MIGRATION_CLOCK_ATTRIBUTE = "blizzard.migration_clock"

#: The logger a revision reports on; alembic's own ``NullHandler`` on ``alembic`` leaves it reaching no stream.
REVISION_LOGGER = "alembic.runtime.migration"

_log = get_logger(__name__)


class _RevisionWarnings(logging.Handler):
    """Hands each warning a revision logs to structlog, so it reaches the stderr of the migrate command that ran it."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)

    def emit(self, record: logging.LogRecord) -> None:
        _log.warning("store migration warning", revision_message=record.getMessage())


@contextmanager
def _revision_warnings_surfaced() -> Iterator[None]:
    logger, handler = logging.getLogger(REVISION_LOGGER), _RevisionWarnings()
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)


class MigrationConnectionError(RuntimeError):
    """Raised when a migration cannot reach the store, in place of the driver's own ``OperationalError``."""


class MigrationRefused(RuntimeError):
    """Raised by a revision that refuses a store it cannot carry across safely, leaving the store as it was; the
    message says what to repair or restore."""


class MigrationClockMissingError(RuntimeError):
    """Raised when a revision that stamps a time runs without a clock to read it from."""


class RevisionMismatchError(RuntimeError):
    """Raised when a store's applied revision differs from the code's expected head; the message names
    the command that reconciles them."""

    def __init__(self, *, store: str, current: str | None, expected: str | None, remedy: str) -> None:
        self.store = store
        self.current = current
        self.expected = expected
        self.remedy = remedy
        super().__init__(
            f"{store} store is at revision {current or '(unmigrated)'}, "
            f"but this build expects {expected or '(none)'}. "
            f"Run `{remedy}` before starting the daemon."
        )


class StoreBelowFloorError(RuntimeError):
    """An absorbed store revision requires migration by an older release first."""

    def __init__(self, *, store: str, current: str, floor: str) -> None:
        self.store = store
        self.current = current
        self.floor = floor
        super().__init__(
            f"{store} store is at revision {current}, below this build's migration baseline. "
            f"Install {floor} and migrate this store through it first."
        )


@dataclass(frozen=True)
class MigrationRunner:
    """Drives one Alembic tree against one store URL. ``clock`` is what a revision that stamps a
    time reads (``migration_clock``); inspecting a store, or a tree no revision of which stamps a
    time, needs none."""

    script_location: Path
    url: str
    clock: IClock | None = None

    def _config(self) -> Config:
        cfg = Config()
        cfg.set_main_option("script_location", str(self.script_location))
        cfg.set_main_option("sqlalchemy.url", self.url)
        if self.clock is not None:
            cfg.attributes[MIGRATION_CLOCK_ATTRIBUTE] = self.clock
        return cfg

    def _ensure_wide_version_table(self) -> None:
        engine = create_engine_from_url(self.url)
        try:
            metadata = MetaData()
            version_num = Column("version_num", String(_VERSION_TABLE_COLUMN_LENGTH), primary_key=True)
            Table("alembic_version", metadata, version_num)
            metadata.create_all(engine, checkfirst=True)
        finally:
            engine.dispose()

    def upgrade(self, revision: str = "head") -> None:
        """Apply pending revisions up to ``revision`` (idempotent — a no-op when current)."""
        try:
            self.refuse_below_floor(store=self._store_label())
            self._ensure_wide_version_table()
            with _revision_warnings_surfaced():
                command.upgrade(self._config(), revision)
        except OperationalError as exc:
            raise MigrationConnectionError(str(exc)) from exc

    def downgrade(self, revision: str) -> None:
        """Reverse revisions down to ``revision`` (``"base"`` unwinds the whole tree)."""
        self.refuse_below_floor(store=self._store_label())
        with _revision_warnings_surfaced():
            command.downgrade(self._config(), revision)

    def _store_label(self) -> str:
        """A store location useful to an operator, without URL credentials or query parameters."""
        url = make_url(self.url)
        if url.get_backend_name() == "sqlite":
            return f"SQLite store at {url.database}"
        return f"{url.get_backend_name()} store {url.database} on {url.host}"

    def script_head(self) -> str | None:
        """The head revision the code carries (the tree's latest script)."""
        return ScriptDirectory.from_config(self._config()).get_current_head()

    def _base_metadata(self) -> tuple[str | None, frozenset[str]]:
        script = ScriptDirectory.from_config(self._config())
        base = script.get_base()
        if base is None:
            return None, frozenset()
        module = script.get_revision(base).module
        return getattr(module, "BASELINE_FLOOR", None), getattr(module, "ABSORBED", frozenset())

    @property
    def baseline_floor(self) -> str | None:
        """Release carrying the baseline, if this tree declares one."""
        return self._base_metadata()[0]

    @property
    def absorbed(self) -> frozenset[str]:
        """Revision ids folded into the baseline, empty for an unfurled tree."""
        return self._base_metadata()[1]

    def refuse_below_floor(self, *, store: str, current: str | None = None) -> None:
        """Refuse only revisions the baseline absorbed, before any store write."""
        current = current if current is not None else self.current_revision()
        if current is None or self.knows_revision(current):
            return
        floor, absorbed = self._base_metadata()
        if current in absorbed and floor is not None:
            raise StoreBelowFloorError(store=store, current=current, floor=floor)

    def knows_revision(self, revision: str) -> bool:
        """Whether the code's tree holds ``revision`` — ``False`` for a store a newer build migrated."""
        try:
            return ScriptDirectory.from_config(self._config()).get_revision(revision) is not None
        except (CommandError, ResolutionError):
            return False

    def reaches(self, revision: str | None, floor: str) -> bool:
        """Whether ``revision`` is ``floor`` or a revision built on it; ``False`` for an unmigrated store."""
        if revision is None:
            return False
        script = ScriptDirectory.from_config(self._config())
        return floor in {step.revision for step in script.iterate_revisions(revision, "base")}

    def current_revision(self) -> str | None:
        """The revision applied to the store, or ``None`` if unmigrated."""
        # The write-path factory switches SQLite to WAL on connect. Revision inspection must not
        # change an older store's journal mode before a below-floor refusal can stop the command.
        engine = create_engine(self.url, future=True)
        try:
            with engine.connect() as conn:
                return MigrationContext.configure(conn).get_current_revision()
        finally:
            engine.dispose()

    def is_current(self) -> bool:
        """True when the store is migrated exactly to the code's head."""
        return self.current_revision() == self.script_head()

    def check_current(self, *, store: str, remedy: str) -> None:
        """Raise :class:`RevisionMismatchError` unless the store is at head."""
        current = self.current_revision()
        expected = self.script_head()
        if current != expected:
            self.refuse_below_floor(store=f"{store} ({self._store_label()})", current=current)
            raise RevisionMismatchError(store=store, current=current, expected=expected, remedy=remedy)


def migration_clock() -> IClock:
    """The clock the running migration was given — what a revision that stamps a time reads.

    Called from inside a revision's ``upgrade``/``downgrade``; raises
    :class:`MigrationClockMissingError` when the migration was run without one."""
    config = op.get_context().config
    clock: IClock | None = None if config is None else config.attributes.get(MIGRATION_CLOCK_ATTRIBUTE)
    if clock is None:
        raise MigrationClockMissingError(
            "this revision stamps a time, but the migration was run without a clock: "
            "migrate through MigrationRunner with a clock"
        )
    return clock
