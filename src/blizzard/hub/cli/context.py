"""The hub an operator verb talks to, and how it prints what comes back."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from http import HTTPStatus
from typing import Any, Protocol, TypedDict, cast

import click

from blizzard.cli.collaborators import ClientFactory, HTTPError, Response
from blizzard.cli.operator_trace import ITraceHeaders
from blizzard.cli.refusal import refusal_of
from blizzard.foundation.operator_sessions import IReadSessionStore
from blizzard.foundation.roles import dto
from blizzard.wire.problem import (
    AUTHENTICATION_TYPES,
    FLAG_OFF,
    PROJECT_REQUIRED,
    TENANT_NOT_FOUND,
    TENANT_REQUIRED,
)
from blizzard.wire.problem_reading import ProblemReading, is_kind
from blizzard.wire.tenant import NO_SUCH_TENANT


class View(Protocol):
    def lines(self) -> Iterable[str]: ...


class PaginationCursor(TypedDict, total=False):
    next_cursor: str | None


# The hub the client verbs talk to: ``BZ_HUB_URL`` overrides the colocated default (band +2).
ENV_HUB_URL = "BZ_HUB_URL"
DEFAULT_HUB_URL = "http://127.0.0.1:8421"
# The tenant the tenant-scoped verbs act in: ``--tenant`` overrides it; an empty value counts as unset.
ENV_HUB_TENANT = "BZ_HUB_TENANT"
CLIENT_TIMEOUT = 15.0

#: The actionable hint a verb's own unnamed 401 maps to.
_LOGIN_HINT = "not authenticated — run `blizzard hub login`"

#: The fallback a verb's own unnamed 403 falls back to when the body carries no ``detail``.
_FORBIDDEN_FALLBACK = "forbidden"

#: The header a call names its tenant in; sent only when the call names one.
TENANT_HEADER = "X-Blizzard-Tenant"

#: The ``detail.code`` a hub before problem details answered, on a refusal that carried no ``type``.
_LEGACY_TENANT_REQUIRED = "tenant_required"
_LEGACY_PROJECT_REQUIRED = "project_required"
_LEGACY_FLAG_OFF = "flag_off"


@dto
@dataclass(frozen=True)
class RawBody:
    """A request body sent as it is, under its own ``Content-Type`` — for a document the hub decodes itself."""

    content: bytes
    media_type: str


@dataclass(frozen=True)
class CliContext:
    """One operator verb's invocation — the resolved hub, the read-only local session
    seam (``bzh:controller-read-only``), where its requests' trace headers come from, whether to print JSON,
    and the tenant the call names — ``None`` leaves it to the hub, which acts in the caller's only membership."""

    hub_url: str
    session_reader: IReadSessionStore
    trace: ITraceHeaders
    client_factory: ClientFactory
    as_json: bool = False
    tenant: str | None = None

    @classmethod
    def of(
        cls,
        hub_url: str | None,
        session_reader: IReadSessionStore,
        trace: ITraceHeaders,
        client_factory: ClientFactory,
        as_json: bool = False,
        tenant: str | None = None,
    ) -> CliContext:
        return cls(
            hub_url=hub_url or os.environ.get(ENV_HUB_URL, DEFAULT_HUB_URL),
            session_reader=session_reader,
            trace=trace,
            client_factory=client_factory,
            as_json=as_json,
            tenant=tenant,
        )

    def get(
        self,
        path: str,
        operation: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Response:
        return self._verb("get", path, operation, params=params)

    def get_all(
        self,
        path: str,
        operation: str,
        *,
        key: str,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, object]]:
        """Every row of a cursor-paginated list read: ``key``'s rows concatenated across
        pages of up to 1000, following ``next_cursor`` until it is absent."""
        page_params = dict(params or {})
        page_params["limit"] = "1000"
        rows: list[dict[str, object]] = []
        cursor: str | None = None
        while True:
            if cursor is not None:
                page_params["cursor"] = cursor
            body: dict[str, object] = self.get(path, operation, params=page_params).json()
            rows.extend(cast(list[dict[str, object]], body[key]))
            cursor = cast(PaginationCursor, body).get("next_cursor")
            if cursor is None:
                return rows

    def post(
        self,
        path: str,
        operation: str,
        *,
        json_body: object | None = None,
        timeout: float = CLIENT_TIMEOUT,
        if_match: int | None = None,
        door: bool = False,
        params: dict[str, str] | None = None,
        raw_body: RawBody | None = None,
    ) -> Response:
        return self._verb(
            "post",
            path,
            operation,
            json_body=json_body,
            params=params,
            timeout=timeout,
            if_match=if_match,
            door=door,
            raw_body=raw_body,
        )

    def patch(
        self,
        path: str,
        operation: str,
        *,
        json_body: object | None = None,
        if_match: int | None = None,
        door: bool = False,
    ) -> Response:
        return self._verb("patch", path, operation, json_body=json_body, if_match=if_match, door=door)

    def put(
        self,
        path: str,
        operation: str,
        *,
        json_body: object | None = None,
        if_match: int | None = None,
        door: bool = False,
    ) -> Response:
        return self._verb("put", path, operation, json_body=json_body, if_match=if_match, door=door)

    def delete(
        self,
        path: str,
        operation: str,
        *,
        json_body: object | None = None,
        params: dict[str, str] | None = None,
    ) -> Response:
        return self._verb("delete", path, operation, json_body=json_body, params=params)

    def stream(
        self,
        path: str,
        operation: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Iterator[str]:
        """A ``GET`` read one decoded line at a time. Any refusal raises before the first
        line, through the same common refusals as :meth:`send` and then :meth:`check`."""
        full_url = f"{self.hub_url.rstrip('/')}{path}"
        try:
            with (
                self.client_factory(timeout=CLIENT_TIMEOUT) as client,
                client.stream("GET", full_url, params=params, headers=self._headers() or None) as resp,
            ):
                if resp.status_code >= 400:
                    resp.read()
                self._refuse_common(resp)
                self.check(resp, operation)
                yield from resp.iter_lines()
        except HTTPError as exc:
            raise self.failed(operation, exc) from exc

    def send(
        self,
        method: str,
        path: str,
        *,
        json_body: object | None = None,
        params: dict[str, str] | None = None,
        timeout: float = CLIENT_TIMEOUT,
        if_match: int | None = None,
        door: bool = False,
        raw_body: RawBody | None = None,
    ) -> Response:
        """The call itself, its status left for the caller to read — except the refusals no
        verb answers itself, which raise here: the tenant (:meth:`_refuse_tenant`), a missing
        project (:meth:`_refuse_project`), and a feature flag that is off (:meth:`_refuse_flag_off`)."""
        full_url = f"{self.hub_url.rstrip('/')}{path}"
        kwargs: dict[str, Any] = {}
        if json_body is not None:
            kwargs["json"] = json_body
        if raw_body is not None:
            kwargs["content"] = raw_body.content
        if params is not None:
            kwargs["params"] = params
        headers = self._headers(door=door)
        if raw_body is not None:
            headers["Content-Type"] = raw_body.media_type
        if if_match is not None:
            headers["If-Match"] = str(if_match)
        if headers:
            kwargs["headers"] = headers
        try:
            with self.client_factory(timeout=timeout) as client:
                resp = client.request(method.upper(), full_url, **kwargs)
        except HTTPError as exc:
            raise self.failed(f"{method.upper()} {path}", exc) from exc
        self._refuse_common(resp)
        return resp

    def _refuse_common(self, resp: Response) -> None:
        """Raise the refusals no verb answers itself, reading the body once."""
        if _succeeded(resp):
            return
        reading = refusal_of(resp)
        self._refuse_tenant(resp, reading)
        self._refuse_project(reading)
        self._refuse_flag_off(reading)

    def check(self, resp: Response, operation: str) -> None:
        """Raise a ``ClickException`` for an error response, in the hub's own words — its ``detail``, else its
        ``title``. An authentication refusal hints at login; a response with no words of its own reports the failure."""
        if _succeeded(resp):
            return
        reading = refusal_of(resp)
        if reading.type in AUTHENTICATION_TYPES or (
            reading.type is None and resp.status_code == HTTPStatus.UNAUTHORIZED
        ):
            raise click.ClickException(_LOGIN_HINT)
        if reading.message is not None:
            raise click.ClickException(reading.message)
        if reading.type is None and resp.status_code == HTTPStatus.FORBIDDEN:
            raise click.ClickException(_FORBIDDEN_FALLBACK)
        try:
            resp.raise_for_status()
        except HTTPError as exc:
            raise self.failed(operation, exc) from exc

    def failed(self, operation: str, exc: Exception) -> click.ClickException:
        return click.ClickException(f"{operation} failed: {exc}")

    def echo_json(self, payload: object) -> None:
        click.echo(json.dumps(payload))

    def show(self, payload: object, view: View) -> None:
        """Print what a verb read: the raw ``payload`` under ``--json``, else the view's
        lines — never both, so a payload no view could format still prints raw."""
        if self.as_json:
            self.echo_json(payload)
            return
        for line in view.lines():
            click.echo(line)

    def show_lines(self, payload: object, *lines: str) -> None:
        """:meth:`show` for output small enough that a verb composes it inline."""
        if self.as_json:
            self.echo_json(payload)
            return
        for line in lines:
            click.echo(line)

    def finish(self, resp: Response, message: str) -> None:
        """Echo a write verb's result: the raw body under ``--json``, else a static
        success line that never has to parse the body at all."""
        if self.as_json:
            self.echo_json(resp.json())
            return
        click.echo(message)

    def _verb(
        self,
        method: str,
        path: str,
        operation: str,
        *,
        json_body: object | None = None,
        params: dict[str, str] | None = None,
        timeout: float = CLIENT_TIMEOUT,
        if_match: int | None = None,
        door: bool = False,
        raw_body: RawBody | None = None,
    ) -> Response:
        resp = self.send(
            method,
            path,
            json_body=json_body,
            params=params,
            timeout=timeout,
            if_match=if_match,
            door=door,
            raw_body=raw_body,
        )
        self.check(resp, operation)
        return resp

    def _refuse_tenant(self, resp: Response, reading: ProblemReading) -> None:
        """Raise when the hub refuses the tenant the call acts in, before a verb can read the status code as its own:
        a ``tenant-required`` refusal (the caller belongs to several tenants and the call named none), or a
        ``tenant-not-found`` one for the tenant ``--tenant`` named. Either lists the caller's tenants from ``/api/me``,
        ids beside names. A ``tenant-not-found`` on a call naming no tenant stays the hub's own words: it is the caller
        holding no membership, or a hub-level verb's own tenant argument."""
        if is_kind(reading, TENANT_REQUIRED, _LEGACY_TENANT_REQUIRED):
            raise self._tenant_refusal(
                "you belong to several tenants — name the one to act in with --tenant <tenant_id>"
            )
        if reading.type == TENANT_NOT_FOUND or (
            reading.type is None and resp.status_code == HTTPStatus.NOT_FOUND and reading.message == NO_SUCH_TENANT
        ):
            if self.tenant is None:
                raise click.ClickException(reading.message or NO_SUCH_TENANT)
            raise self._tenant_refusal(
                f"no tenant {self.tenant} you can enter — it does not exist, it is closed, or you are not its member"
            )

    def _refuse_project(self, reading: ProblemReading) -> None:
        """Raise :func:`project_required` on a ``project-required`` refusal, with the projects it carries."""
        if is_kind(reading, PROJECT_REQUIRED, _LEGACY_PROJECT_REQUIRED):
            raise project_required(_projects_of(reading))

    def _refuse_flag_off(self, reading: ProblemReading) -> None:
        """Raise on a ``flag-off`` refusal — the route is held behind a flag that is off — in its own words."""
        if is_kind(reading, FLAG_OFF, _LEGACY_FLAG_OFF):
            raise click.ClickException(reading.message or "not available yet: its flag is off")

    def _tenant_refusal(self, headline: str) -> click.ClickException:
        """``headline``, then the tenants the caller can name with ``--tenant``. ``/api/me`` is asked without the
        refused tenant's header, which would refuse it too."""
        me = replace(self, tenant=None).send("get", "/api/me")
        tenants = _tenants_of(me) if me.status_code == HTTPStatus.OK else []
        lines = [headline]
        if tenants:
            lines.append("your tenants:")
            lines.extend(f"  {t['tenant_id']}  {t['name']}  ({t['role']})" for t in tenants)
        return click.ClickException("\n".join(lines))

    def _headers(self, *, door: bool = False) -> dict[str, str]:
        """``X-Blizzard-Door: cli`` when ``door``; the ``Authorization: Bearer`` header
        for this hub — absent when the local session store holds none, so every verb keeps working
        with no login; ``X-Blizzard-Tenant`` only when the call names its tenant; and the running
        operator command's ``traceparent`` when it is traced."""
        token = self.session_reader.load(self.hub_url)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        door_header = {"X-Blizzard-Door": "cli"} if door else {}
        tenant_header = {TENANT_HEADER: self.tenant} if self.tenant else {}
        return {**door_header, **headers, **tenant_header, **self.trace.headers()}


def _succeeded(resp: Response) -> bool:
    """Whether ``resp`` is a ``2xx`` — an unfollowed redirect is not a success a verb may report."""
    return HTTPStatus.OK <= resp.status_code < HTTPStatus.MULTIPLE_CHOICES


def project_required(projects: list[dict[str, str]]) -> click.ClickException:
    """The refusal of a call that needs a project and named none while the tenant holds several: ``projects``
    (each with ``project_id``, ``slug`` and ``name``) listed, ids beside slugs and names, asking for ``--project``."""
    lines = ["this tenant holds several projects — name the one to act in with --project <id|slug>"]
    if projects:
        lines.append("its projects:")
        lines.extend(f"  {p['project_id']}  {p['slug']}  {p['name']}" for p in projects)
    return click.ClickException("\n".join(lines))


def _tenants_of(me: Response) -> list[dict[str, str]]:
    """The ``tenants`` rows of an ``/api/me`` body that carry an id, a name and a role."""
    try:
        body = me.json()
    except ValueError:
        return []
    rows = cast(dict[str, object], body).get("tenants") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return []
    keys = ("tenant_id", "name", "role")
    return [
        {k: str(row[k]) for k in keys}
        for entry in cast(list[object], rows)
        if isinstance(entry, dict)
        for row in [cast(dict[str, object], entry)]
        if all(row.get(k) for k in keys)
    ]


def _projects_of(reading: ProblemReading) -> list[dict[str, str]]:
    """The ``projects`` rows of a ``project-required`` refusal that carry an id, a slug and a name."""
    detail = reading.detail
    rows = cast(dict[str, object], detail).get("projects") if isinstance(detail, dict) else None
    if not isinstance(rows, list):
        return []
    keys = ("project_id", "slug", "name")
    return [
        {k: str(row[k]) for k in keys}
        for entry in cast(list[object], rows)
        if isinstance(entry, dict)
        for row in [cast(dict[str, object], entry)]
        if all(row.get(k) for k in keys)
    ]
