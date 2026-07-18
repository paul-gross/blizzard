"""Runner-bearer-token authentication — the edge dependency alongside ``get_services``
(issue #86a).

``require_runner_principal`` resolves a presented ``Authorization: Bearer`` token to a
:class:`RunnerPrincipal`, reading only through :attr:`~blizzard.hub.composition.HubServices.registry`
(the read-only registry Protocol, held directly here exactly as ``chunks.py`` holds
``services.chunks`` — ``bzh:controller-read-only`` permits a read-only repository at the
edge). The lookup hashes the presented token to its sha256 hex digest and resolves it via
``registration_for_token_hash`` — a reverse, hash-indexed read. That lookup **is** the
match: because it selects on the stored hash column, no separate ``hmac.compare_digest``
is load-bearing for resolution (unlike a verify-then-compare flow over a value already in
hand).

``config.runner_auth_mode`` selects the rollout posture: ``warn`` (the default) logs the
offending condition and returns ``None`` so the route still runs unauthenticated;
``enforce`` raises 401. A missing/malformed header and an unresolved token are treated
identically — both warn-log-and-pass, or both 401.

``assert_owns`` is the separate, per-route confinement helper: a route that reads its own
``runner_id`` out of its body/path calls it with the resolved principal to reject a token
presented for a *different* runner — ``enforce`` raises 403, ``warn`` logs. It stays out of
the dependency itself because a router-level dependency cannot uniformly read a declared
``runner_id`` (body for some routes, path for others).

``reject_runner_principal`` (issue #87) is the mirror-image router-level dependency for
every *operator* router: a runner principal is confined to the fleet router, so a bearer
token that resolves to one on an operator verb is rejected outright — not silently treated
as an anonymous call plus an ignored credential. It shares ``_resolve_principal`` with
``require_runner_principal`` (both hash-lookup the same way); they differ only in which
outcome each treats as the failure — a *missing/invalid* token for
``require_runner_principal``, a *resolved* one for ``reject_runner_principal``. A missing or
unresolvable token is not itself an operator-verb failure — anonymous is exactly what an
operator call is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from blizzard.foundation.logging import get_logger
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.config import RUNNER_AUTH_ENFORCE
from blizzard.hub.domain.enrollment import hash_token

_log = get_logger("blizzard.hub.auth")

_BEARER_PREFIX = "Bearer "


@dataclass(frozen=True)
class RunnerPrincipal:
    """A bearer token resolved to the runner it belongs to."""

    runner_id: str
    workspace_id: str


def _resolve_principal(request: Request, services: HubServices) -> RunnerPrincipal | None:
    """Resolve a presented ``Authorization: Bearer`` token to a principal, or ``None``
    when the header is missing/malformed or the token does not resolve — no mode
    logic, no rejection; both dependencies below layer their own failure semantics
    on top of this shared lookup."""
    header = request.headers.get("authorization", "")
    if not header.startswith(_BEARER_PREFIX):
        return None
    token = header[len(_BEARER_PREFIX) :]
    token_hash = hash_token(token)
    registration = services.registry.registration_for_token_hash(token_hash)
    if registration is None:
        return None
    return RunnerPrincipal(runner_id=registration.runner_id, workspace_id=registration.workspace_id)


def require_runner_principal(
    request: Request, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerPrincipal | None:
    """Resolve the presented bearer token to a principal, or ``None`` under ``warn``.

    Under ``enforce`` a missing/malformed header or an unresolved token raises 401;
    under ``warn`` (the default) the same conditions are logged and the route runs
    with no principal — callers that need per-runner confinement combine this with
    :func:`assert_owns`."""
    mode = request.app.state.config.runner_auth_mode
    principal = _resolve_principal(request, services)
    if principal is None:
        reason = (
            "missing or malformed Authorization header"
            if not request.headers.get("authorization", "").startswith(_BEARER_PREFIX)
            else "bearer token does not resolve to a known runner"
        )
        return _reject(mode, path=request.url.path, reason=reason)
    return principal


def reject_runner_principal(request: Request, services: Annotated[HubServices, Depends(get_services)]) -> None:
    """Reject a request on an operator router whose bearer token resolves to a runner
    principal — a runner's token is valid only on the fleet router (issue #87). A
    missing or unresolvable token is not flagged here — that is exactly what an
    anonymous operator call looks like; ``require_runner_principal`` is where a
    *missing* token matters, not this one.

    ``enforce`` raises 403; ``warn`` (the default) logs and lets the call proceed,
    matching every other rollout-mode behavior in this module."""
    mode = request.app.state.config.runner_auth_mode
    principal = _resolve_principal(request, services)
    if principal is None:
        return
    detail = f"runner token for {principal.runner_id!r} is not valid on an operator verb"
    if mode == RUNNER_AUTH_ENFORCE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    _log.warning("runner token presented on operator verb", runner_id=principal.runner_id, path=request.url.path)


def assert_owns(principal: RunnerPrincipal | None, runner_id: str, *, mode: str) -> None:
    """Reject a request whose declared ``runner_id`` differs from the resolved principal's.

    A ``None`` principal (an unresolved token under ``warn``) is not itself flagged here —
    ``require_runner_principal`` already warn-logged the missing/invalid credential; this
    only fires once a token *did* resolve, to a runner other than the one the request
    declares."""
    if principal is None or principal.runner_id == runner_id:
        return
    detail = f"token belongs to runner {principal.runner_id!r}, not the declared {runner_id!r}"
    if mode == RUNNER_AUTH_ENFORCE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    _log.warning("runner_id mismatch", declared_runner_id=runner_id, token_runner_id=principal.runner_id)


def _reject(mode: str, *, path: str, reason: str) -> RunnerPrincipal | None:
    if mode == RUNNER_AUTH_ENFORCE:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=reason)
    _log.warning("runner auth failed", reason=reason, path=path)
    return None
