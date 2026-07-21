"""The injected error-wrapping seam for the identity repositories (issue #91).

Follows the exemplar's ``RepoErrorFactory`` shape
(``../../../../blizzard-harness/exemplars/python/repo_pattern.py``): a library
exception (a SQLAlchemy ``IntegrityError`` — a username/email/provider-subject
collision race, or a session-id-hash collision) is translated into the domain
:class:`RepoError` at the one site it is caught, logged once at ERROR
(``bzh:structlog-logging``) so no call site re-logs it.
"""

from __future__ import annotations

import structlog


class RepoError(Exception):
    """Raised by an identity-repository write that hit an unexpected store fault —
    never for an *expected* collision a caller already checks for up front (e.g. the
    username-collision-suffix minting loop), only for a fault a caller could not have
    anticipated (a raced insert, a disconnected store)."""

    def __init__(self, message: str, *, operation: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.operation = operation
        self.detail = detail


class RepoErrorFactory:
    """The injected error-wrapping seam every ``hub/auth/internal/`` adapter takes."""

    def __init__(self, log: structlog.stdlib.BoundLogger) -> None:
        self._log = log

    def from_integrity_error(self, exc: Exception, message: str, *, operation: str = "") -> RepoError:
        """Wrap a raced/unexpected ``IntegrityError`` into a :class:`RepoError`, logged
        once at ERROR. Callers must not log it again."""
        detail = str(exc).strip()
        self._log.error(message, operation=operation, detail=detail)
        return RepoError(message, operation=operation, detail=detail)
