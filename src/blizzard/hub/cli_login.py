"""``blizzard hub login``'s client mechanics (issue #96) — PKCE minting, the ephemeral
loopback listener, and the paste-code fallback. No call here reaches a provider: every
one targets the hub itself."""

from __future__ import annotations

import http.server
import secrets
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from blizzard.hub.auth.pkce import challenge_from_verifier

_CLIENT_ID = "cli"
_LOOPBACK_HOST = "127.0.0.1"
#: The paste-code fallback's out-of-band redirect — its own literal, so this client module imports no server module.
OOB_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
#: How long the loopback listener waits for the browser before giving up.
CALLBACK_TIMEOUT_SECONDS = 300.0
_EXCHANGE_TIMEOUT = 15.0


class LoginError(Exception):
    """Any step of the login dance failed."""


@dataclass(frozen=True)
class Pkce:
    verifier: str
    challenge: str

    @classmethod
    def new(cls) -> Pkce:
        verifier = secrets.token_urlsafe(48)
        return cls(verifier, challenge_from_verifier(verifier))


class Callback:
    """The one-shot ``127.0.0.1`` listener bound before the browser is sent anywhere."""

    def __init__(self, expected_state: str) -> None:
        self.code: str | None = None
        self.error: str | None = None
        self._server = http.server.HTTPServer((_LOOPBACK_HOST, 0), self._handler(expected_state))

    @property
    def redirect_uri(self) -> str:
        return f"http://{_LOOPBACK_HOST}:{self._server.server_address[1]}/callback"

    def wait(self, timeout: float) -> None:
        self._server.timeout = timeout
        self._server.handle_request()
        self._server.server_close()

    def _handler(self, expected_state: str) -> type[http.server.BaseHTTPRequestHandler]:
        result = self

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


@dataclass(frozen=True)
class Login:
    """One ``blizzard hub login`` dance against one hub — the client half of the hub's ``Delivery``."""

    base_url: str
    redirect_uri: str
    state: str
    pkce: Pkce

    @classmethod
    def loopback(cls, base_url: str, *, open_browser: bool = True, timeout: float = CALLBACK_TIMEOUT_SECONDS) -> Login:
        pkce = Pkce.new()
        state = secrets.token_urlsafe(18)
        callback = Callback(state)
        return Loopback(base_url, callback.redirect_uri, state, pkce, callback, open_browser, timeout)

    @classmethod
    def paste_code(cls, base_url: str, *, prompt_for_code: Callable[[], str]) -> Login:
        pkce = Pkce.new()
        state = secrets.token_urlsafe(18)
        return PasteCode(base_url, OOB_REDIRECT_URI, state, pkce, prompt_for_code)

    @property
    def authorize_url(self) -> str:
        query = urlencode(
            {
                "client": _CLIENT_ID,
                "redirect_uri": self.redirect_uri,
                "state": self.state,
                "code_challenge": self.pkce.challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.base_url.rstrip('/')}/api/auth/authorize?{query}"

    def token(self) -> str:
        return self._exchange(self._code())

    def _code(self) -> str:
        raise NotImplementedError

    def _exchange(self, code: str) -> str:
        resp = httpx.post(
            f"{self.base_url.rstrip('/')}/api/auth/cli/token",
            json={"code": code, "code_verifier": self.pkce.verifier, "redirect_uri": self.redirect_uri},
            timeout=_EXCHANGE_TIMEOUT,
        )
        if resp.status_code != 200:
            raise LoginError(
                "the hub rejected the login exchange — the code, PKCE verifier, or redirect_uri did not match"
            )
        body = resp.json()
        token = body.get("token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise LoginError("the hub's login exchange response carried no token")
        return token


@dataclass(frozen=True)
class Loopback(Login):
    """The browser flow: an ephemeral ``127.0.0.1`` port takes the code back."""

    callback: Callback
    open_browser: bool
    timeout: float

    def _code(self) -> str:
        url = self.authorize_url
        if self.open_browser:
            webbrowser.open(url)
        else:
            print(f"open this URL to log in: {url}")
        self.callback.wait(self.timeout)
        if self.callback.code is None:
            raise LoginError(self.callback.error or "timed out waiting for the browser login to complete")
        return self.callback.code


@dataclass(frozen=True)
class PasteCode(Login):
    """The headless fallback: ``prompt_for_code`` is a seam over ``click.prompt``, so a test needs no terminal."""

    prompt_for_code: Callable[[], str]

    def _code(self) -> str:
        print(f"open this URL to log in, then paste the code it shows: {self.authorize_url}")
        return self.prompt_for_code()
