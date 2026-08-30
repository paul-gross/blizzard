"""The injected error-wrapping seam for ``hub/store/internal/`` (blizzard#413).

Shaped after ``blizzard.hub.auth.errors``: a driver exception is translated into the
domain :class:`HubStoreError` at the one site it is caught, logged once at ERROR
(``bzh:structlog-logging``) so no call site re-logs it. Unlike the auth seam, the wrap
site is a connection-acquiring collaborator that encloses the caller's whole unit of
work — not just acquisition — so a fault anywhere inside a ``with`` block is caught
here too."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import structlog
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError


class HubStoreError(Exception):
    """Raised by a ``hub/store/internal/`` adapter that hit an unexpected driver
    fault — never for an *expected* collision a caller catches and recovers from
    locally (a first-write-wins race), only for a fault a caller could not have
    anticipated."""

    def __init__(self, message: str, *, operation: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.operation = operation
        self.detail = detail


class HubStoreErrorFactory:
    """The injected error-wrapping seam :class:`HubStoreConnections` wraps a driver
    fault through."""

    def __init__(self, log: structlog.stdlib.BoundLogger) -> None:
        self._log = log

    def from_driver(self, exc: Exception, *, operation: str) -> HubStoreError:
        """Wrap `exc` into a :class:`HubStoreError`, logged once at ERROR. Callers
        must not log it again."""
        detail = str(exc).strip()
        message = f"hub store {operation} failed: {detail}"
        self._log.error(message, operation=operation, detail=detail)
        return HubStoreError(message, operation=operation, detail=detail)


class HubStoreConnections:
    """The connection-acquiring collaborator every ``hub/store/internal/`` adapter
    takes in place of ``Engine`` (``bzh:dependency-injection``) — the single
    wrap-and-log site for the whole package. ``read``/``write`` enclose the caller's
    whole unit of work, so a fault raised anywhere inside — not just at connection
    acquisition — is caught and wrapped here."""

    def __init__(self, engine: Engine, errors: HubStoreErrorFactory) -> None:
        self._engine = engine
        self._errors = errors

    @contextmanager
    def read(self, operation: str) -> Iterator[Connection]:
        try:
            with self._engine.connect() as conn:
                yield conn
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation=operation) from exc

    @contextmanager
    def write(self, operation: str, *, expect: tuple[type[BaseException], ...] = ()) -> Iterator[Connection]:
        """Like :meth:`read`, over a transaction. ``expect`` names an exception a
        caller already catches and recovers from locally (a first-write-wins race,
        D3) — passed through unwrapped rather than treated as a driver fault."""
        try:
            with self._engine.begin() as conn:
                yield conn
        except expect:
            raise
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation=operation) from exc
