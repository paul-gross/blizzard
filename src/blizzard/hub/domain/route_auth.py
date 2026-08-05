"""Route-token authorization (issue #84b) — does the caller of a chunk-scoped write or fence-advancing
fact hold the chunk's **currently-live** acquisition?

A plain function over already-loaded values (``bzh:domain-takes-objects``), not a service.
``route_token_mode`` is a **separate** rollout brake from ``runner_auth_mode``. Comparison is
constant-time against the sha256 hex digest :func:`hash_token` produces, the same one the mint uses."""

from __future__ import annotations

import hmac

from blizzard.foundation.logging import get_logger
from blizzard.hub.config import ROUTE_TOKEN_ENFORCE
from blizzard.hub.domain.enrollment import hash_token
from blizzard.hub.domain.work import ChunkFacts, newest_live_route_token

_log = get_logger("blizzard.hub.route_auth")


def check_route_token(
    facts: ChunkFacts,
    *,
    presented_token: str | None,
    submission_runner_id: str,
    route_runner_id: str | None,
    mode: str,
) -> str | None:
    """The route-token check, in order: (1) the presented token hashes to the chunk's live route token;
    (2) ``submission_runner_id`` matches the live route's runner. Never runs an epoch fence itself.
    Returns a failure detail to reject with under ``enforce``, or ``None`` to proceed — either the
    check passed, or ``mode`` is ``warn`` and the failure was only logged."""
    live_token = newest_live_route_token(facts.routes_created, facts.routes_released, facts.route_tokens_minted)
    if live_token is None:
        detail: str | None = "chunk has no live route — nothing to authorize this write against"
    elif presented_token is None or not hmac.compare_digest(hash_token(presented_token), live_token.token_hash):
        detail = "route token missing or does not match the chunk's live route"
    elif submission_runner_id != route_runner_id:
        detail = f"runner_id {submission_runner_id!r} does not hold the chunk's live route"
    else:
        detail = None
    if detail is None:
        return None
    if mode == ROUTE_TOKEN_ENFORCE:
        return detail
    _log.warning(
        "route token check failed",
        detail=detail,
        submission_runner_id=submission_runner_id,
        route_runner_id=route_runner_id,
    )
    return None
