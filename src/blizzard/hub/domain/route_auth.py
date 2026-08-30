"""Route-token authorization (issue #84b) — does the caller of a chunk-scoped write or fence-advancing
fact hold the chunk's **currently-live** acquisition?

A value over already-loaded values (``bzh:domain-takes-objects``), not a service. ``route_token_mode``
is a **separate** rollout brake from ``runner_auth_mode``. Comparison is constant-time against the
sha256 hex digest :class:`TokenHash` produces, the same one the mint uses."""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from blizzard.foundation.logging import get_logger
from blizzard.foundation.tokens import TokenHash
from blizzard.hub.config import ROUTE_TOKEN_ENFORCE
from blizzard.hub.domain.work import ChunkFacts, RouteHistory

_log = get_logger("blizzard.hub.route_auth")


@dataclass(frozen=True)
class RouteToken:
    """A chunk's live route acquisition judged against one submission's presented token."""

    facts: ChunkFacts
    presented: str | None
    submission_runner_id: str
    route_runner_id: str | None

    def rejection(self, *, mode: str) -> str | None:
        """The check, in order: (1) the presented token hashes to the chunk's live route token;
        (2) ``submission_runner_id`` matches the live route's runner. Never runs an epoch fence
        itself. Returns a failure detail to reject with under ``enforce``, or ``None`` to proceed —
        either the check passed, or ``mode`` is ``warn`` and the failure was only logged."""
        live_token = RouteHistory.of(self.facts).newest_token
        if live_token is None:
            detail: str | None = "chunk has no live route — nothing to authorize this write against"
        elif self.presented is None or not hmac.compare_digest(TokenHash(self.presented).hex, live_token.token_hash):
            detail = "route token missing or does not match the chunk's live route"
        elif self.submission_runner_id != self.route_runner_id:
            detail = f"runner_id {self.submission_runner_id!r} does not hold the chunk's live route"
        else:
            detail = None
        if detail is None:
            return None
        if mode == ROUTE_TOKEN_ENFORCE:
            return detail
        _log.warning(
            "route token check failed",
            detail=detail,
            submission_runner_id=self.submission_runner_id,
            route_runner_id=self.route_runner_id,
        )
        return None
