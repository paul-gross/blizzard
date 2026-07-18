"""Route-token authorization — the check a chunk-scoped write or fence-advancing
runner fact must pass before its own semantics run (issue #84b).

Layered on top of runner identity (``hub/api/auth.py``'s ``require_runner_principal`` /
``assert_owns``, issue #86a): where those confirm *which runner* is calling, this
confirms the caller holds the chunk's **currently-live acquisition** — the route
capability token minted at claim (``hub/domain/claim.py``, issue #84a) and derived by
:func:`~blizzard.hub.domain.work.newest_live_route_token`.

The check is a plain function, not a service — it takes already-loaded values
(``bzh:domain-takes-objects``): the caller resolves ``facts`` via ``load_facts`` and
the live route's runner_id via ``route_of`` (``ChunkFacts``'s own route-created facts
carry no runner_id — only ``hub/domain/fleet.py``'s ``Route`` does), so this stays a
pure function callable from both :mod:`~blizzard.hub.domain.apply` (completions and
gate resolutions) and :mod:`~blizzard.hub.domain.facts` (the buffered fact intake).

``route_token_mode`` (``hub/config.py``) is a **separate** rollout brake from
``runner_auth_mode`` — ``warn`` (the default) logs a missing/mismatched token and lets
the caller proceed; ``enforce`` returns a rejection detail. Comparison is constant-time
(``hmac.compare_digest``) against the sha256 hex digest — the same :func:`hash_token`
the mint (``claim.py``) and re-key paths use, so all three can never drift onto
different digests of the same secret.
"""

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
    """The route-token check, in order: (1) the presented token hashes to the chunk's
    live route token; (2) ``submission_runner_id`` matches the live route's runner.
    Never runs the epoch fence itself — the caller's own fence stays untouched and
    runs after this (order-3 in the plan).

    Returns a failure detail to reject with under ``enforce``, or ``None`` to proceed
    (either the check passed, or ``mode`` is ``warn`` and the failure was only logged).
    """
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
