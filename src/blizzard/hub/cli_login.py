"""``blizzard hub login``'s own mechanics (issue #96) — PKCE challenge/verifier
minting, the ephemeral loopback listener, and the paste-code fallback.

Kept out of ``hub/cli.py`` so the click glue there stays thin (mirrors
``hub/graphs.py`` holding the graph-YAML mechanics ``chunk_migrate`` calls into). The
CLI never contacts a provider — every network call here targets the hub itself: the
browser is pointed at the hub's own ``authorize`` endpoint (issue #95), and the code it
delivers is redeemed at ``POST /api/auth/cli/token``.
"""

from __future__ import annotations

import http.server
import secrets
import webbrowser
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from blizzard.hub.auth.pkce import challenge_from_verifier

_CLIENT_ID = "cli"
_LOOPBACK_HOST = "127.0.0.1"
#: The out-of-band redirect form the paste-code fallback registers as (mirrors
#: ``hub/api/idp.py``'s own ``CLI_OOB_REDIRECT_URI`` — kept as a separate literal
#: rather than an import so this client-side module carries no dependency on the
#: server route module).
OOB_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
#: How long the loopback listener waits for the browser to complete the hub login
#: before giving up.
CALLBACK_TIMEOUT_SECONDS = 300.0
_EXCHANGE_TIMEOUT = 15.0


class LoginError(Exception):
    """Any step of the login dance failed — the CLI command wraps this in a
    ``click.ClickException``."""


def _new_pkce_pair() -> tuple[str, str]:
    """``(code_verifier, code_challenge)`` — a fresh, high-entropy verifier and its
    S256 challenge (``blizzard.hub.auth.pkce``, the exact function the hub's own
    exchange calls, so the two sides can never drift onto a different encoding)."""
    verifier = secrets.token_urlsafe(48)
    return verifier, challenge_from_verifier(verifier)


def _authorize_url(base_url: str, *, redirect_uri: str, state: str, code_challenge: str) -> str:
    query = urlencode(
        {
            "client": _CLIENT_ID,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{base_url.rstrip('/')}/api/auth/authorize?{query}"


def _exchange(base_url: str, *, code: str, code_verifier: str, redirect_uri: str) -> str:
    resp = httpx.post(
        f"{base_url.rstrip('/')}/api/auth/cli/token",
        json={"code": code, "code_verifier": code_verifier, "redirect_uri": redirect_uri},
        timeout=_EXCHANGE_TIMEOUT,
    )
    if resp.status_code != 200:
        raise LoginError("the hub rejected the login exchange — the code, PKCE verifier, or redirect_uri did not match")
    body = resp.json()
    token = body.get("token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        raise LoginError("the hub's login exchange response carried no token")
    return token


class _CallbackResult:
    def __init__(self) -> None:
        self.code: str | None = None
        self.error: str | None = None


def _make_handler(result: _CallbackResult, expected_state: str) -> type[http.server.BaseHTTPRequestHandler]:
    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass  # silence the default stderr access log

        def do_GET(self) -> None:
            query = parse_qs(urlparse(self.path).query)
            state = query.get("state", [None])[0]
            if state != expected_state:
                result.error = "state mismatch"
            else:
                result.code = query.get("code", [None])[0]
                result.error = query.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            message = (
                "Login complete — you can close this tab and return to the terminal."
                if not result.error
                else f"Login failed: {result.error}"
            )
            self.wfile.write(f"<!doctype html><html><body>{message}</body></html>".encode())

    return _Handler


def loopback_login(base_url: str, *, open_browser: bool = True, timeout: float = CALLBACK_TIMEOUT_SECONDS) -> str:
    """Bind an ephemeral ``127.0.0.1`` port, open the browser to the hub's authorize
    endpoint, wait for the one callback request, and exchange the delivered code (with
    the PKCE verifier) for a hub session token. ``timeout`` (default
    :data:`CALLBACK_TIMEOUT_SECONDS`) is a constructor parameter rather than a bare
    module constant so a test can drive a fast, deterministic "browser never shows up"
    case."""
    verifier, challenge = _new_pkce_pair()
    state = secrets.token_urlsafe(18)
    result = _CallbackResult()
    server = http.server.HTTPServer((_LOOPBACK_HOST, 0), _make_handler(result, state))
    port = server.server_address[1]
    redirect_uri = f"http://{_LOOPBACK_HOST}:{port}/callback"
    url = _authorize_url(base_url, redirect_uri=redirect_uri, state=state, code_challenge=challenge)

    if open_browser:
        webbrowser.open(url)
    else:
        print(f"open this URL to log in: {url}")

    server.timeout = timeout
    server.handle_request()
    server.server_close()
    if result.code is None:
        raise LoginError(result.error or "timed out waiting for the browser login to complete")
    return _exchange(base_url, code=result.code, code_verifier=verifier, redirect_uri=redirect_uri)


def paste_code_login(base_url: str, *, prompt_for_code) -> str:  # type: ignore[no-untyped-def]
    """The headless fallback: render the hub's authorize URL with the out-of-band
    redirect form, print it, and exchange the code ``prompt_for_code`` returns (a
    thin seam over ``click.prompt`` so a test can drive this with no terminal)."""
    verifier, challenge = _new_pkce_pair()
    state = secrets.token_urlsafe(18)
    url = _authorize_url(base_url, redirect_uri=OOB_REDIRECT_URI, state=state, code_challenge=challenge)
    print(f"open this URL to log in, then paste the code it shows: {url}")
    code = prompt_for_code()
    return _exchange(base_url, code=code, code_verifier=verifier, redirect_uri=OOB_REDIRECT_URI)
