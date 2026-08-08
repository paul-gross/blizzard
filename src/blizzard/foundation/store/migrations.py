"""The Alembic migration runner and the revision-mismatch guard.

Schema change is Alembic, applied manually through the CLI, never at daemon startup
(``bzh:manual-migrations``). The runner drives *one* migration tree, pointed at a ``script_location``
and a store ``url``; the guard refuses to run on a revision mismatch, naming the migrate command."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Column, MetaData, String, Table

from blizzard.foundation.store.engine import create_engine_from_url

# Alembic's default `version_num String(32)` truncates `YYYYMMDD_HHMM_slug` ids on postgres (#191).
# Pinned by tests/test_pin_foundation.py::test_the_version_table_admits_this_projects_revision_ids.
_VERSION_TABLE_COLUMN_LENGTH = 255


class RevisionMismatchError(RuntimeError):
    """Raised when a store's applied revision differs from the code's expected head; the message names
    the command to run, so a skew fails loud instead of a schema being rewritten under running data."""

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


@dataclass(frozen=True)
class MigrationRunner:
    """Drives one Alembic tree against one store URL."""

    script_location: Path
    url: str

    def _config(self) -> Config:
        cfg = Config()
        cfg.set_main_option("script_location", str(self.script_location))
        cfg.set_main_option("sqlalchemy.url", self.url)
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
        self._ensure_wide_version_table()
        command.upgrade(self._config(), revision)

    def downgrade(self, revision: str) -> None:
        """Reverse revisions down to ``revision`` (``"base"`` unwinds the whole tree)."""
        command.downgrade(self._config(), revision)

    def script_head(self) -> str | None:
        """The head revision the code carries (the tree's latest script)."""
        return ScriptDirectory.from_config(self._config()).get_current_head()

    def current_revision(self) -> str | None:
        """The revision applied to the store, or ``None`` if unmigrated."""
        engine = create_engine_from_url(self.url)
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
            raise RevisionMismatchError(store=store, current=current, expected=expected, remedy=remedy)
