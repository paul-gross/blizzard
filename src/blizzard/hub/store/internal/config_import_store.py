"""SQLAlchemy adapter for the legacy-config carry-over (package-private).

One ``store.write`` runs the whole import through the per-record create bodies — so each keeps its
in-transaction checks — and appends the ``config_import`` fact last. The fact is re-checked first inside
the same transaction, so two racing imports write once."""

from __future__ import annotations

import json

from sqlalchemy import func, insert, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.hub.domain.config.carry_over import (
    CommitCoordinate,
    ConfigImportFact,
    IConfigImportWriter,
    IReadConfigImports,
    LegacyImport,
    LegacyImportRefused,
)
from blizzard.hub.domain.config.repositories import (
    RepositoryCoordinateTaken,
    RepositoryNameTaken,
    RepositorySecretUnavailable,
)
from blizzard.hub.domain.config.work_sources import (
    WorkSourceLocatorTaken,
    WorkSourceNameTaken,
    WorkSourceSecretUnavailable,
)
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal import repository_record_store as repository_writes
from blizzard.hub.store.internal import work_source_record_store as work_source_writes
from blizzard.hub.store.internal.secret_store import insert_secret
from blizzard.hub.store.schema import artifacts, config_import_facts

#: What a create body's in-transaction checks raise when a concurrent write took its claim first.
_REFUSALS = (
    WorkSourceNameTaken,
    WorkSourceLocatorTaken,
    WorkSourceSecretUnavailable,
    RepositoryNameTaken,
    RepositoryCoordinateTaken,
    RepositorySecretUnavailable,
)


class _AlreadyImported(Exception):
    """Raised inside the transaction when an import was recorded first, so nothing commits."""


def _recorded(conn: Connection) -> bool:
    return conn.execute(select(config_import_facts.c.id).limit(1)).first() is not None


def _append_fact(conn: Connection, fact: ConfigImportFact) -> None:
    read = {
        "config_path": fact.read.config_path,
        "sources": list(fact.read.sources),
        "variables": list(fact.read.variables),
    }
    conn.execute(
        insert(config_import_facts).values(imported_at=fact.imported_at, actor=fact.actor, read=json.dumps(read))
    )


class ConfigImportStore:
    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def recorded(self) -> bool:
        with self._store.read("recorded") as conn:
            return _recorded(conn)

    def commit_coordinates(self) -> list[CommitCoordinate]:
        repo = func.coalesce(artifacts.c.repo, artifacts.c.name)
        with self._store.read("commit_coordinates") as conn:
            rows = conn.execute(
                select(artifacts.c.forge, repo.label("repo"))
                .where(artifacts.c.kind == ArtifactKind.GIT_COMMIT.value)
                .distinct()
                .order_by(artifacts.c.forge, repo)
            ).all()
        return [CommitCoordinate(forge=row.forge, repo=row.repo) for row in rows]

    def write(self, imported: LegacyImport) -> bool:
        try:
            with self._store.write("import", expect=(_AlreadyImported, LegacyImportRefused)) as conn:
                if _recorded(conn):
                    raise _AlreadyImported
                for secret in imported.secrets:
                    try:
                        insert_secret(
                            conn,
                            secret.name,
                            sealed=secret.sealed,
                            at=secret.change.recorded_at,
                            by=secret.change.actor,
                            change=secret.change,
                        )
                    except IntegrityError as exc:  # a concurrent create won the race
                        raise LegacyImportRefused(f"secret {secret.name} was created while importing") from exc
                try:
                    for source, change in imported.work_sources:
                        work_source_writes.insert_record(conn, source, change)
                    for repository, change in imported.repositories:
                        repository_writes.insert_record(conn, repository, change)
                except _REFUSALS as exc:
                    raise LegacyImportRefused(str(exc)) from exc
                _append_fact(conn, imported.fact)
        except _AlreadyImported:
            return False
        return True


def _conforms_config_import_reader(x: ConfigImportStore) -> IReadConfigImports:
    return x


def _conforms_config_import_writer(x: ConfigImportStore) -> IConfigImportWriter:
    return x
