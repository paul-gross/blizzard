"""The forge-row lock: a forge's retire and enable, its edit, and every referrer write lock the named forge's row first.

Static proof, no live Postgres: each write body's first statement — the one right after the scoped write's own
tenant-row lock — is compiled under both dialects and must be ``UPDATE forges SET``, so a retire and an enable racing
on one forge wait for each other's commit rather than leaving an enabled record naming a retired forge."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import Connection, create_engine
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.sql.dml import Update
from structlog import get_logger

from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, ConfigChange, Door, RecordKind
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeFields, ForgeId
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryFields, RepositoryId
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource, WorkSourceFields, WorkSourceId
from blizzard.hub.store.errors import HubStoreErrorFactory
from blizzard.hub.store.internal import forge_record_store, repository_record_store, work_source_record_store
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import StoreScope, TenantId

pytestmark = pytest.mark.unit

_AT = datetime(2026, 10, 25, tzinfo=UTC)
_FORGE_ID = ForgeId("frg_1")
_CHANGE = ConfigChange.of(
    ChangeContext(actor="op", door=Door.API), RecordKind.FORGE, _FORGE_ID, 1, ChangeOp.RETIRE, (), _AT
)
_FORGE = ConfiguredForge(
    forge_id=_FORGE_ID,
    slug="github",
    name="GitHub",
    kind="github",
    fields=ForgeFields(api_base="https://api.github.com", web_base=None),
    revision=2,
    created_at=_AT,
    created_by="op",
)
_SOURCE = ConfiguredWorkSource(
    work_source_id=WorkSourceId("wsrc_1"),
    slug="demo",
    name="demo",
    fields=WorkSourceFields(provider="github", locator="acme/demo", forge_id=_FORGE_ID, annotate=False, secret="gh"),
    revision=2,
    created_at=_AT,
    created_by="op",
)
_REPO = ConfiguredRepository(
    repository_id=RepositoryId("repo_1"),
    slug="widget",
    name="widget",
    fields=RepositoryFields(_FORGE_ID, "acme", "widget", "main", "gh"),
    revision=2,
    created_at=_AT,
    created_by="op",
)


class _Stop(Exception):
    """Raised after the first statement — only that one is under test."""


class _FirstStatementConn:
    """A fake ``Connection`` that records the first statement handed to ``execute`` and stops the body there."""

    def __init__(self, scoped: Connection) -> None:
        self.statements: list[object] = []
        self.get_execution_options = scoped.get_execution_options

    def execute(self, stmt: object) -> None:
        self.statements.append(stmt)
        raise _Stop


@contextmanager
def _scoped_connection() -> Iterator[Connection]:
    connections = ScopedConnections(
        create_engine("sqlite://"), HubStoreErrorFactory(get_logger("test")), StoreScope(TenantId("ten_test"))
    )
    with connections.read("capture") as conn:
        yield conn


_WRITES: dict[str, Callable[[Connection], None]] = {
    "forge retire": lambda conn: forge_record_store.record_forge_lifecycle(
        conn, _FORGE, retired=True, from_revision=1, at=_AT, by="op", change=_CHANGE
    ),
    "forge enable": lambda conn: forge_record_store.record_forge_lifecycle(
        conn, _FORGE, retired=False, from_revision=1, at=_AT, by="op", change=_CHANGE
    ),
    "forge edit": lambda conn: forge_record_store.update_forge(conn, _FORGE, 1, None, _CHANGE),
    "work-source create": lambda conn: work_source_record_store.insert_record(conn, _SOURCE, _CHANGE),
    "work-source edit": lambda conn: work_source_record_store.update_record(conn, _SOURCE, 1, None, _CHANGE),
    "work-source enable": lambda conn: work_source_record_store.record_lifecycle_fact(
        conn, _SOURCE, retired=False, from_revision=1, at=_AT, by="op", change=_CHANGE
    ),
    "repository create": lambda conn: repository_record_store.insert_record(conn, _REPO, _CHANGE),
    "repository edit": lambda conn: repository_record_store.update_record(conn, _REPO, 1, None, _CHANGE),
    "repository enable": lambda conn: repository_record_store.record_lifecycle_fact(
        conn, _REPO, retired=False, from_revision=1, at=_AT, by="op", change=_CHANGE
    ),
}


@pytest.mark.parametrize("write", list(_WRITES))
def test_the_write_locks_the_forge_row_before_any_guard_read(write: str) -> None:
    with _scoped_connection() as scoped:
        conn = _FirstStatementConn(scoped)
        with pytest.raises(_Stop):
            _WRITES[write](conn)  # type: ignore[arg-type]

    lock_stmt = conn.statements[0]
    assert isinstance(lock_stmt, Update)
    assert str(lock_stmt.compile(dialect=postgresql.dialect())).startswith("UPDATE forges SET")
    assert str(lock_stmt.compile(dialect=sqlite.dialect())).startswith("UPDATE forges SET")
    assert lock_stmt.compile().params["forge_id_1"] == _FORGE_ID
