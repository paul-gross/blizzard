"""The runner-store error-wrapping seam.

Mirrors ``blizzard.hub.store.errors``: a driver exception is translated into the domain
:class:`RunnerStoreError` at the one site it is caught, logged once at ERROR
(``bzh:structlog-logging``) so no call site re-logs it."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any

import structlog
from sqlalchemy import Connection, Engine, Row, Select
from sqlalchemy.exc import SQLAlchemyError


class RunnerStoreError(RuntimeError):
    """A runner-store operation failed — the domain-facing error the loop sees.

    Wraps the driver exception at the adapter boundary, so callers never depend on it."""


class RunnerStoreErrorFactory:
    """The injected error-wrapping seam every ``runner/store/internal/`` adapter takes
    in place of a module-level logger — the substitutability the hub-store seam's
    ``HubStoreErrorFactory`` also gives its own adapters."""

    def __init__(self, log: structlog.stdlib.BoundLogger) -> None:
        self._log = log

    def from_driver(self, exc: Exception, *, operation: str) -> RunnerStoreError:
        """Wrap `exc` into a :class:`RunnerStoreError`, logged once at ERROR. Callers
        must not log it again."""
        detail = str(exc).strip()
        self._log.error("runner store operation failed", operation=operation, detail=detail)
        return RunnerStoreError(f"runner store {operation} failed: {detail}")


class RunnerStoreConnections:
    """The connection-acquiring collaborator every ``runner/store/internal/`` adapter
    takes in place of ``Engine`` (``bzh:dependency-injection``)."""

    def __init__(self, engine: Engine, errors: RunnerStoreErrorFactory) -> None:
        self._engine = engine
        self._errors = errors

    def connect(self) -> Connection:
        try:
            return self._engine.connect()
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation="connect") from exc

    def begin(self) -> AbstractContextManager[Connection]:
        try:
            return self._engine.begin()
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation="begin") from exc

    def all(self, stmt: Select[Any]) -> list[Row[Any]]:
        try:
            with self._engine.connect() as conn:
                return list(conn.execute(stmt))
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation="query") from exc
