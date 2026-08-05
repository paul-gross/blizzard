"""The hub-as-IdP surface (issues #95, #96) — ``authorize``, ``jwks.json``, and the
``client=cli`` PKCE code exchange.

Public plane throughout: an unauthenticated browser must reach ``authorize`` to *start*
authenticating. Under ``auth.mode = "none"`` every route here 404s — no keypair, no IdP.
"""

from __future__ import annotations

import html
import json
import re
import secrets
from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from blizzard.auth_core import USER_MANAGE
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, resolve_identity
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.service import CLI_CLIENT_ID
from blizzard.hub.config import AUTH_MODE_NONE
from blizzard.hub.domain.registry import RunnerRegistration

router = APIRouter(prefix="/api/auth", tags=["auth"])

#: The minted JWT's lifetime — the issue's own ceiling (``exp <= 60s``).
JWT_TTL = timedelta(seconds=60)

_RESPONSE_MODES = {"form_post", "fragment"}

#: The ``cli`` client id's built-in redirect form (issue #96) — an ephemeral
#: ``127.0.0.1`` loopback callback, or the fixed out-of-band paste-code marker.
_CLI_LOOPBACK_REDIRECT_RE = re.compile(r"^http://127\.0\.0\.1:\d+/callback$")
CLI_OOB_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"


def _valid_cli_redirect_uri(redirect_uri: str) -> bool:
    return redirect_uri == CLI_OOB_REDIRECT_URI or bool(_CLI_LOOPBACK_REDIRECT_RE.match(redirect_uri))


def _resolve_client(services, client: str) -> RunnerRegistration | None:  # type: ignore[no-untyped-def]
    """Resolve an authorize ``client`` id to its registered redirect set — only a
    registered runner is a valid client on this branch."""
    return services.registry.get_runner(client)


@router.get("/authorize", response_model=None)
def authorize(
    request: Request,
    client: str,
    redirect_uri: str,
    state: str,
    response_mode: str = "form_post",
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
) -> HTMLResponse | RedirectResponse:
    if request.app.state.config.auth.mode == AUTH_MODE_NONE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="the IdP surface is not enabled")
    if response_mode not in _RESPONSE_MODES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown response_mode {response_mode!r}")
    services = get_services(request)
    if services.signing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="the IdP surface is not enabled")

    is_cli = client == CLI_CLIENT_ID
    if is_cli:
        if not _valid_cli_redirect_uri(redirect_uri):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="client=cli's redirect_uri must be the registered ephemeral loopback form",
            )
        if not code_challenge or code_challenge_method not in (None, "S256"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="client=cli requires a PKCE code_challenge (S256) — PKCE is mandatory for this public client",
            )
    else:
        registration = _resolve_client(services, client)
        if registration is None or redirect_uri not in registration.redirect_uris:
            # One undifferentiated 400 for both cases — the open-redirect guard (AC):
            # a caller cannot fingerprint valid client ids by probing.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="unknown client or unregistered redirect_uri"
            )

    identity = resolve_identity(request, services)
    if identity is None:
        providers = services.oauth_providers.list()
        if not providers:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="no hub session, and no configured providers to authenticate against",
            )
        return_to = request.url.path
        if request.url.query:
            return_to = f"{return_to}?{request.url.query}"
        if len(providers) == 1:
            # Single-provider fast path (AC): no chooser hop — the dance lands the
            # browser back on this exact URL with a session (decision D5).
            return RedirectResponse(f"/api/auth/{providers[0].name}/authorize?return_to={quote(return_to, safe='')}")
        # Two or more providers (issue #128): no single dance to auto-run, so hand the
        # browser to a chooser carrying this pending request as its return target.
        return RedirectResponse(f"/login?return_to={quote(return_to, safe='')}")

    user = services.users.get(identity.user_id)
    if is_cli:
        assert user is not None, f"resolved identity {identity.user_id!r} has no backing user row"
        assert code_challenge is not None  # already validated above
        code = services.auth.mint_cli_code(user, code_challenge=code_challenge, redirect_uri=redirect_uri)
        return _cli_delivery(redirect_uri=redirect_uri, code=code, state=state)

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


class CliTokenRequest(BaseModel):
    """``POST /api/auth/cli/token``'s body (issue #96) — the CLI's PKCE code exchange."""

    code: str
    code_verifier: str
    redirect_uri: str


class CliTokenResponse(BaseModel):
    """The minted hub session token (decision D6) — never a runner-style JWT."""

    token: str


@router.post("/cli/token", response_model=CliTokenResponse)
def cli_token(request: Request, body: CliTokenRequest) -> CliTokenResponse:
    """Redeem a ``client=cli`` authorize code for a hub session token (issue #96).

    Public plane — there is no session yet; this route is what mints one. One
    undifferentiated 400 covers every failure, telling a caller nothing about which."""
    if request.app.state.config.auth.mode == AUTH_MODE_NONE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="the IdP surface is not enabled")
    services = get_services(request)
    token = services.auth.exchange_cli_code(body.code, code_verifier=body.code_verifier, redirect_uri=body.redirect_uri)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired code, PKCE verifier, or redirect_uri",
        )
    return CliTokenResponse(token=token)


@router.post(
    "/rotate-signing-key",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(reject_runner_principal), Depends(require(USER_MANAGE))],
)
def rotate_signing_key(request: Request) -> Response:
    """Mint a fresh current signing key, demoting the old current to previous (issue
    #95). Human-plane, gated on ``user:manage`` and closed to a runner bearer token."""
    if request.app.state.config.auth.mode == AUTH_MODE_NONE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="the IdP surface is not enabled")
    services = get_services(request)
    if services.signing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="the IdP surface is not enabled")
    services.signing.rotate()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _mint_jti() -> str:
    return secrets.token_urlsafe(18)


def _cli_delivery(*, redirect_uri: str, code: str, state: str) -> HTMLResponse | RedirectResponse:
    """Deliver a ``client=cli`` authorize code (issue #96) — a 302 carrying the code in
    the query string for the loopback form, or a paste-able page for out-of-band."""
    if redirect_uri == CLI_OOB_REDIRECT_URI:
        return _paste_code_page(code)
    separator = "&" if "?" in redirect_uri else "?"
    target = f"{redirect_uri}{separator}code={quote(code, safe='')}&state={quote(state, safe='')}"
    return RedirectResponse(target, status_code=status.HTTP_302_FOUND)


def _paste_code_page(code: str) -> HTMLResponse:
    """The paste-code fallback's rendered page (issue #96)."""
    escaped = html.escape(code)
    body = (
        "<!doctype html><html><body>"
        "<p>Copy this code and paste it into the waiting <code>blizzard hub login</code> prompt:</p>"
        f'<pre style="font-size:1.5em">{escaped}</pre>'
        "</body></html>"
    )
    return HTMLResponse(body)


def _delivery_page(*, redirect_uri: str, token: str, state: str, response_mode: str) -> HTMLResponse:
    """Render the token delivery — **never** a query string (AC): either an
    auto-submitting ``form_post`` or a client-side redirect into the URL fragment."""
    if response_mode == "fragment":
        target = f"{redirect_uri}#token={token}&state={state}"
        # `json.dumps` alone is not script-context-safe against a `</script>` breakout;
        # `target` cannot carry one here, but the substitution is defensive.
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
