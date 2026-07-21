"""The hub-as-IdP surface (issue #95) — ``GET /api/auth/authorize`` and
``GET /api/auth/jwks.json``.

Public plane throughout, exactly like ``hub/api/auth_login.py``: an unauthenticated
runner-bound browser must reach ``authorize`` to *start* authenticating, and
``jwks.json`` is by definition public key material. Under ``auth.mode = "none"`` both
routes 404 — there is no keypair, no JWKS, no IdP surface (mirrors #92's "no login
mechanism under none").

``authorize`` serves **registered public clients generally**, not just runners — a
``client`` is resolved by treating it as a runner id first (the only client kind this
phase registers); #96's ``client=cli`` rides this same route later by extending the
client-resolution step, never by forking a second authorize handler.

**Chaining through the provider dance.** When the browser presents no hub session, the
authorize request must first complete a #92 provider login and land back here with one
established. This route reuses #92's own single-use ``auth_state``/``return_to``
mechanism unmodified (decision D5) — it redirects into
``GET /api/auth/{provider}/authorize?return_to=<this request's own path+query>``, and
that route's ``callback`` already redirects to ``return_to`` once a session is minted,
landing the browser back on this exact URL, this time with a cookie. This phase resolves
the *single configured provider* case only (the common deployment shape, and the one the
issue's acceptance criteria exercise) — zero or multiple configured providers has no
automatic chooser to bounce through from a bare page load (unlike the web app's own
``/login``, which is driven client-side and already supports a multi-provider button
list), so authorize refuses with 501 in that case rather than guessing; an operator with
multiple providers configured logs into the board first, and the resolved session then
lets ``authorize`` proceed on any subsequent bounce.
"""

from __future__ import annotations

import html
import json
import secrets
from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from blizzard.auth_core import USER_MANAGE
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, resolve_identity
from blizzard.hub.api.deps import get_services
from blizzard.hub.config import AUTH_MODE_NONE
from blizzard.hub.domain.registry import RunnerRegistration

router = APIRouter(prefix="/api/auth", tags=["auth"])

#: The minted JWT's lifetime — the issue's own ceiling (``exp <= 60s``); the runner
#: additionally honors a further ±30s clock-skew leeway on top of this.
JWT_TTL = timedelta(seconds=60)

_RESPONSE_MODES = {"form_post", "fragment"}


def _resolve_client(services, client: str) -> RunnerRegistration | None:  # type: ignore[no-untyped-def]
    """Resolve an authorize ``client`` id to its registered redirect set. Only a
    registered runner is a valid client in this phase (#96 later extends this to the
    ``cli`` public client, on this same function, never a second handler)."""
    return services.registry.get_runner(client)


@router.get("/authorize", response_model=None)
def authorize(
    request: Request,
    client: str,
    redirect_uri: str,
    state: str,
    response_mode: str = "form_post",
) -> HTMLResponse | RedirectResponse:
    if request.app.state.config.auth.mode == AUTH_MODE_NONE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="the IdP surface is not enabled")
    if response_mode not in _RESPONSE_MODES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown response_mode {response_mode!r}")
    services = get_services(request)
    if services.signing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="the IdP surface is not enabled")

    registration = _resolve_client(services, client)
    if registration is None or redirect_uri not in registration.redirect_uris:
        # Deliberately one undifferentiated 400 for "unknown client" and "unregistered
        # redirect_uri" — the open-redirect guard (AC): neither tells a caller which of
        # the two failed, so a client can't fingerprint valid client ids by probing.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="unknown client or unregistered redirect_uri"
        )

    identity = resolve_identity(request, services)
    if identity is None:
        providers = services.oauth_providers.list()
        if len(providers) != 1:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="no hub session, and interactive provider selection is not supported from this endpoint "
                f"with {len(providers)} configured provider(s) — log into the hub board first",
            )
        return_to = request.url.path
        if request.url.query:
            return_to = f"{return_to}?{request.url.query}"
        return RedirectResponse(f"/api/auth/{providers[0].name}/authorize?return_to={quote(return_to, safe='')}")

    user = services.users.get(identity.user_id)
    email = user.email if user is not None else None
    claims = {
        "sub": identity.user_id,
        "username": identity.username,
        "email": email,
        "role": identity.role.value,
        "aud": client,
        "jti": _mint_jti(),
    }
    token = services.signing.sign(claims, now=services.clock.now(), ttl=JWT_TTL)
    return _delivery_page(redirect_uri=redirect_uri, token=token, state=state, response_mode=response_mode)


@router.get("/jwks.json")
def jwks(request: Request) -> dict[str, object]:
    if request.app.state.config.auth.mode == AUTH_MODE_NONE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="the IdP surface is not enabled")
    services = get_services(request)
    if services.signing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="the IdP surface is not enabled")
    return services.signing.public_jwks()


@router.post(
    "/rotate-signing-key",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(reject_runner_principal), Depends(require(USER_MANAGE))],
)
def rotate_signing_key(request: Request) -> Response:
    """``blizzard hub rotate-signing-key`` (issue #95) — mint a fresh current key,
    demoting the old current to previous. Human-plane, gated on ``user:manage`` (the
    same admin-tier permission the user-management API uses — no new permission is
    minted for this one verb) and closed to a runner's own bearer token
    (``reject_runner_principal``, mirroring every other operator router)."""
    if request.app.state.config.auth.mode == AUTH_MODE_NONE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="the IdP surface is not enabled")
    services = get_services(request)
    if services.signing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="the IdP surface is not enabled")
    services.signing.rotate()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _mint_jti() -> str:
    return secrets.token_urlsafe(18)


def _delivery_page(*, redirect_uri: str, token: str, state: str, response_mode: str) -> HTMLResponse:
    """Render the token delivery — **never** a query string (AC): either an
    auto-submitting ``form_post`` (what the runner, a plain HTTP backend, actually
    consumes) or a client-side redirect into the URL fragment (structurally present for
    a future browser-side consumer, e.g. #96's CLI loopback page; unused by this
    phase's own runner federation, which reads the posted form)."""
    if response_mode == "fragment":
        target = f"{redirect_uri}#token={token}&state={state}"
        # `json.dumps` is not itself script-context-safe against a `</script>` breakout
        # if `target` carried one — it can't here (`token`/`state` are opaque
        # generated values, `redirect_uri` is exact-match validated against an
        # operator-registered URI), but the substitution is defensive belt-and-braces.
        script_safe = json.dumps(target).replace("</", "<\\/")
        body = f"<!doctype html><html><body><script>location.replace({script_safe});</script></body></html>"
        return HTMLResponse(body)
    action = html.escape(redirect_uri, quote=True)
    token_value = html.escape(token, quote=True)
    state_value = html.escape(state, quote=True)
    body = (
        '<!doctype html><html><body onload="document.forms[0].submit()">'
        f'<form method="post" action="{action}">'
        f'<input type="hidden" name="token" value="{token_value}">'
        f'<input type="hidden" name="state" value="{state_value}">'
        "</form></body></html>"
    )
    return HTMLResponse(body)
