"""The Alembic migration runner and the revision-mismatch guard.

Schema change is Alembic, applied manually through the CLI, never at daemon
startup (``bzh:manual-migrations``). Each daemon owns an independent migration
tree; this runner drives *one* tree — pointed at a ``script_location``
and a store ``url`` — and is reused by both. The revision guard is what a daemon
calls at startup to **refuse to run on a schema mismatch**, naming the exact
migrate command to fix it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Column, MetaData, String, Table

from blizzard.foundation.store.engine import create_engine_from_url

# Alembic's own auto-created `alembic_version` table hardcodes `version_num
# String(32)` (`alembic.ddl.impl.DefaultImpl.version_table_impl`) — this project's
# revision ids (`YYYYMMDD_HHMM_slug`) already exceed 32 chars, which sqlite's
# typeless storage silently tolerates but postgres enforces
# (`StringDataRightTruncation`, caught by `blizzard:compose-smoke`, issue #191).
# Alembic's per-dialect `version_table_impl` hook is the sanctioned override point
# but needs a registered `DefaultImpl` subclass per dialect; pre-creating the
# table ourselves — a no-op once it exists — is the one portable fix that needs
# no per-dialect code (`bzh:sql-portable`).
_VERSION_TABLE_COLUMN_LENGTH = 255


def _ensure_wide_version_table(url: str) -> None:
    engine = create_engine_from_url(url)
    try:
        metadata = MetaData()
        version_num = Column("version_num", String(_VERSION_TABLE_COLUMN_LENGTH), primary_key=True)
        Table("alembic_version", metadata, version_num)
        metadata.create_all(engine, checkfirst=True)
    finally:
        engine.dispose()


class RevisionMismatchError(RuntimeError):
    """Raised when a store's applied revision differs from the code's expected head.

    The message names the exact command to run — a version skew fails loud
    instead of a daemon silently rewriting a schema out from under running data.
    """

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

    def upgrade(self, revision: str = "head") -> None:
        """Apply pending revisions up to ``revision`` (idempotent — a no-op when current)."""
        _ensure_wide_version_table(self.url)
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
