"""The hub an operator verb talks to, and how it prints what comes back."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import click
import httpx

from blizzard.hub import session_store

# The hub the client verbs talk to: ``BZ_HUB_URL`` overrides the colocated default (band +2).
ENV_HUB_URL = "BZ_HUB_URL"
DEFAULT_HUB_URL = "http://127.0.0.1:8421"
CLIENT_TIMEOUT = 15.0

#: The actionable hint every verb's own 401 maps to (issue #96), unless the caller named
#: its own 401 entry in ``on_status``.
_LOGIN_HINT = "not authenticated — run `blizzard hub login`"


@dataclass(frozen=True)
class CliContext:
    """One operator verb's invocation — the resolved hub, and whether to print JSON.

    Built inside each verb from that verb's own ``--hub-url``/``--json`` options rather
    than once at the group, so the flags stay where an operator already types them."""

    hub_url: str
    as_json: bool = False

    @classmethod
    def of(cls, hub_url: str | None, as_json: bool = False) -> CliContext:
        return cls(hub_url=hub_url or os.environ.get(ENV_HUB_URL, DEFAULT_HUB_URL), as_json=as_json)

    def get(
        self,
        path: str,
        operation: str,
        *,
        params: dict[str, str] | None = None,
        on_status: dict[int, str] | None = None,
    ) -> httpx.Response:
        return self._verb("get", path, operation, params=params, on_status=on_status)

    def post(
        self, path: str, operation: str, *, json_body: object | None = None, on_status: dict[int, str] | None = None
    ) -> httpx.Response:
        return self._verb("post", path, operation, json_body=json_body, on_status=on_status)

    def patch(
        self, path: str, operation: str, *, json_body: object | None = None, on_status: dict[int, str] | None = None
    ) -> httpx.Response:
        return self._verb("patch", path, operation, json_body=json_body, on_status=on_status)

    def put(
        self, path: str, operation: str, *, json_body: object | None = None, on_status: dict[int, str] | None = None
    ) -> httpx.Response:
        return self._verb("put", path, operation, json_body=json_body, on_status=on_status)

    def send(
        self, method: str, path: str, *, json_body: object | None = None, params: dict[str, str] | None = None
    ) -> httpx.Response:
        """The call itself, unchecked — for a verb that reads a status code of its own first.
        Dispatches through ``httpx``'s module-level verb function so a test's
        ``monkeypatch.setattr`` still intercepts it."""
        full_url = f"{self.hub_url.rstrip('/')}{path}"
        kwargs: dict[str, object] = {"timeout": CLIENT_TIMEOUT}
        if json_body is not None:
            kwargs["json"] = json_body
        if params is not None:
            kwargs["params"] = params
        headers = self._headers()
        if headers:
            kwargs["headers"] = headers
        try:
            return getattr(httpx, method)(full_url, **kwargs)
        except httpx.HTTPError as exc:
            raise self.failed(f"{method.upper()} {path}", exc) from exc

    def check(self, resp: httpx.Response, operation: str, *, on_status: dict[int, str] | None = None) -> None:
        """Map a handful of status codes to a ``ClickException`` reading the body's own
        ``detail`` (falling back to the per-code default named in ``on_status``); anything
        else still errors via ``raise_for_status``. A bare 401 not named in ``on_status``
        gets the actionable login hint (issue #96)."""
        if on_status and resp.status_code in on_status:
            raise click.ClickException(self.detail(resp, on_status[resp.status_code]))
        if resp.status_code == httpx.codes.UNAUTHORIZED:
            raise click.ClickException(_LOGIN_HINT)
        try:
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise self.failed(operation, exc) from exc

    def detail(self, resp: httpx.Response, fallback: str) -> str:
        """The server's own ``detail`` from a JSON error body, or ``fallback``."""
        try:
            body = resp.json()
        except ValueError:
            return fallback
        if isinstance(body, dict):
            detail = body.get("detail")
            if isinstance(detail, str):
                return detail
        return fallback

    def failed(self, operation: str, exc: Exception) -> click.ClickException:
        return click.ClickException(f"{operation} failed: {exc}")

    def echo_json(self, payload: object) -> None:
        click.echo(json.dumps(payload))

    def finish(self, resp: httpx.Response, message: str) -> None:
        """Echo a write verb's result: the raw body under ``--json``, else a static
        success line that never has to parse the body at all."""
        click.echo(json.dumps(resp.json()) if self.as_json else message)

    def _verb(
        self,
        method: str,
        path: str,
        operation: str,
        *,
        json_body: object | None = None,
        params: dict[str, str] | None = None,
        on_status: dict[int, str] | None = None,
    ) -> httpx.Response:
        resp = self.send(method, path, json_body=json_body, params=params)
        self.check(resp, operation, on_status=on_status)
        return resp

    def _headers(self) -> dict[str, str]:
        """The ``Authorization: Bearer`` header for this hub (issue #96) — empty when the
        local session store holds none, so every verb keeps working with no login."""
        token = session_store.load_session(self.hub_url)
        return {"Authorization": f"Bearer {token}"} if token else {}
