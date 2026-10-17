"""The runner-store error seam: a driver exception is translated into the domain :class:`RunnerStoreError` at the
one site it is caught, logged once at ERROR (``bzh:structlog-logging``).

The connections every adapter holds, bound to one runner, live in :mod:`blizzard.runner.store.scoped`."""

from __future__ import annotations

import structlog


class RunnerStoreError(RuntimeError):
    """A runner-store operation failed — the domain-facing error the loop sees.

    Wraps the driver exception at the adapter boundary, so callers never depend on it."""


class RunnerStoreErrorFactory:
    """The injected error-wrapping seam every ``runner/store/internal/`` adapter takes
    in place of a module-level logger, so the logger is substitutable."""

    def __init__(self, log: structlog.stdlib.BoundLogger) -> None:
        self._log = log

    def from_driver(self, exc: Exception, *, operation: str) -> RunnerStoreError:
        """Wrap `exc` into a :class:`RunnerStoreError`, logged once at ERROR. Callers
        must not log it again."""
        detail = str(exc).strip()
        self._log.error("runner store operation failed", operation=operation, detail=detail)
        return RunnerStoreError(f"runner store {operation} failed: {detail}")
