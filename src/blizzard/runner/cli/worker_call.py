"""The identity a spawned worker acts under, and the calls it makes with it."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

import click
import httpx

from blizzard.foundation.trace_ids import DerivedContext, parse_traceparent

if TYPE_CHECKING:
    from blizzard.foundation.cli_spans import CliSpan

# A worker's identity comes from its spawn environment, never a flag; a missing token draws ``403``.
ENV_LEASE_ID = "BLIZZARD_LEASE_ID"
ENV_RUNNER_URL = "BLIZZARD_RUNNER_URL"
ENV_LEASE_TOKEN = "BLIZZARD_LEASE_TOKEN"
LEASE_TOKEN_HEADER = "X-Blizzard-Lease-Token"

ENV_ELICITATION = "BLIZZARD_ELICITATION"

# `TRACEPARENT`, OpenTelemetry's own carrier name, is never read.
ENV_TRACEPARENT = "BLIZZARD_TRACEPARENT"
ENV_CHUNK_ID = "BLIZZARD_CHUNK_ID"
TRACEPARENT_HEADER = "traceparent"
_SAMPLED_FLAG = 0x01
_SESSION_META_KEY = "blizzard.runner.worker_session"

# A hub-proxied read travels further than a runner-local write, so the two are bounded apart.
READ_TIMEOUT = 30.0
WRITE_TIMEOUT = 5.0


@dataclass(frozen=True)
class Problem:
    """A rejected call's response — the ``detail`` string its JSON body carries, or ``""``."""

    response: httpx.Response

    @property
    def detail(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        try:
            body = self.response.json()
        except ValueError:
            return ""
        detail = body.get("detail") if isinstance(body, dict) else None
        return str(detail) if detail else ""


def _exit_code(exc: BaseException) -> int:
    """The process exit code a leaf's exception ends in, as click's ``main`` would turn it."""
    if isinstance(exc, (click.exceptions.Exit, click.ClickException)):
        return exc.exit_code
    if isinstance(exc, SystemExit):
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    return 1


# The seam a test replaces to hand the session a client over a canned transport.
client_factory: Callable[[], httpx.Client] = httpx.Client


class WorkerSession:
    """One worker command's state: the one HTTP client every request and the span post share,
    and the command's span when a trace context was handed down. The ``runner`` group builds and
    finishes it — a short-lived command has its own root, not a singleton."""

    def __init__(self, environ: Mapping[str, str] | None = None, *, group_path: str = "") -> None:
        self._environ = os.environ if environ is None else environ
        self._group_path = group_path
        self._client: httpx.Client | None = None
        self._span: CliSpan | None = None
        self._parent = self._traced_parent()

    @classmethod
    def untraced(cls) -> WorkerSession:
        return cls({})

    @classmethod
    def current(cls) -> WorkerSession:
        """The session of the command being run; a context the ``runner`` group did not build
        gets an untraced one, kept so it still shares one client."""
        ctx = click.get_current_context(silent=True)
        if ctx is None:
            return cls.untraced()
        meta = ctx.find_root().meta
        session = meta.get(_SESSION_META_KEY)
        if session is None:
            session = meta[_SESSION_META_KEY] = cls.untraced()
        return session

    @classmethod
    def begin(cls, ctx: click.Context) -> WorkerSession:
        session = cls(group_path=ctx.command_path)
        ctx.find_root().meta[_SESSION_META_KEY] = session
        return session

    def _traced_parent(self) -> DerivedContext | None:
        """The step's context, only when tracing is on: a parseable, sampled
        ``BLIZZARD_TRACEPARENT`` and a runner to send to. Anything else runs untraced."""
        raw = self._environ.get(ENV_TRACEPARENT)
        if not raw or not self._environ.get(ENV_RUNNER_URL):
            return None
        parent = parse_traceparent(raw)
        if parent is None or not parent.trace_flags & _SAMPLED_FLAG:
            return None
        return parent

    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = client_factory()
        return self._client

    def open_span(self) -> str | None:
        """Start this command's span once — a second identity in the same process reuses it —
        and return the ``traceparent`` to inject, or ``None`` when untraced."""
        if self._parent is None:
            return None
        if self._span is None:
            # Imported only now, so an untraced command never loads the emitter.
            from blizzard.foundation import cli_spans

            ctx = click.get_current_context(silent=True)
            self._span = cli_spans.CliSpan.open(
                self._parent,
                self._command(ctx.command_path if ctx else self._group_path),
                chunk_id=self._environ.get(ENV_CHUNK_ID, ""),
                lease_id=self._environ.get(ENV_LEASE_ID, ""),
            )
        return self._span.traceparent

    def _command(self, command_path: str) -> str:
        """The command's names below the program, always rooted at ``runner`` — never a value."""
        below = command_path.removeprefix(self._group_path).strip()
        return f"runner {below}".strip()

    def finish(self, exit_code: int) -> None:
        """End the command: send the span if one opened, then close the client."""
        span = self._span
        client = self.client() if span is not None else self._client
        self._client = self._span = None
        if client is None:
            return
        released = True
        if span is not None:
            from blizzard.foundation import cli_spans

            span.finish(exit_code)
            token = self._environ.get(ENV_LEASE_TOKEN)
            released = cli_spans.send(
                client,
                self._environ.get(ENV_RUNNER_URL, ""),
                span,
                headers={LEASE_TOKEN_HEADER: token} if token else {},
                environ=self._environ,
            )
        if released:
            client.close()

    def run(self, invoke: Callable[[], object]) -> object:
        """Run a command, then finish the session with the exit code it ends in."""
        code = 0
        try:
            return invoke()
        except BaseException as exc:
            code = _exit_code(exc)
            raise
        finally:
            self.finish(code)


@dataclass(frozen=True)
class WorkerCall:
    """A spawned worker's ambient identity — the runner it reports to, and the lease it acts
    under (``""`` for a verb that names its own chunk instead).

    Every call lands on the runner's local API, which proxies onward to the hub as the runner
    principal where a route needs to: a worker holds no hub credential of its own."""

    verb: str
    runner_url: str
    lease_id: str = ""
    lease_token: str | None = None
    session: WorkerSession | None = None
    traceparent: str | None = None

    @classmethod
    def of(cls, verb: str, *, lease: bool = True) -> WorkerCall:
        """This worker's identity — a hard error rather than the soft skip :meth:`hook` takes,
        so a lost read or write reaches the worker rather than passing silently."""
        runner_url = os.environ.get(ENV_RUNNER_URL)
        lease_id = os.environ.get(ENV_LEASE_ID)
        if not runner_url or (lease and not lease_id):
            wanted = f"{ENV_LEASE_ID}/{ENV_RUNNER_URL}" if lease else ENV_RUNNER_URL
            raise click.ClickException(f"{verb}: no {wanted} in the environment")
        return cls._bound(verb, runner_url, lease_id or "", traced=True)

    @classmethod
    def hook(cls, verb: str, *, traced: bool = True) -> WorkerCall | None:
        """The same identity for a worker *hook*, or ``None`` after saying so on stderr — a
        hook must never break the worker's tool call, so an absent identity skips.

        A hook that fires constantly opts out of tracing with ``traced=False``: it opens no span
        and injects no context."""
        runner_url = os.environ.get(ENV_RUNNER_URL)
        lease_id = os.environ.get(ENV_LEASE_ID)
        if not lease_id or not runner_url:
            click.echo(f"{verb}: no {ENV_LEASE_ID}/{ENV_RUNNER_URL} in the environment; skipping", err=True)
            return None
        return cls._bound(verb, runner_url, lease_id, traced=traced)

    @classmethod
    def _bound(cls, verb: str, runner_url: str, lease_id: str, *, traced: bool) -> WorkerCall:
        session = WorkerSession.current()
        traceparent = session.open_span() if traced else None
        return cls(verb, runner_url, lease_id, os.environ.get(ENV_LEASE_TOKEN), session, traceparent)

    def leased(self, suffix: str) -> str:
        return f"/api/leases/{self.lease_id}/{suffix}"

    def get(
        self, path: str, *, failure: str, rejected: str | None = None, params: dict[str, str] | None = None
    ) -> httpx.Response:
        return self._call("get", path, failure=failure, rejected=rejected, params=params, timeout=READ_TIMEOUT)

    def post(
        self, path: str, *, failure: str, rejected: str | None = None, json_body: object | None = None
    ) -> httpx.Response:
        return self._call("post", path, failure=failure, rejected=rejected, json_body=json_body, timeout=WRITE_TIMEOUT)

    def soft_post(self, path: str, *, failure: str, json_body: object | None = None) -> None:
        try:
            self.post(path, failure=failure, json_body=json_body)
        except click.ClickException as exc:
            click.echo(f"{exc.message}; skipping", err=True)

    def _call(
        self,
        method: str,
        path: str,
        *,
        failure: str,
        timeout: float,
        rejected: str | None = None,
        json_body: object | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        """One call, with this lease's token attached and a failure named as ``verb: failure``.

        A rejection the worker can act on carries its guidance in the body, so that is preferred
        over the bare status line; ``rejected`` names it differently from an unreachable runner
        where that helps."""
        kwargs: dict[str, object] = {"timeout": timeout, "headers": self._headers(), "params": params}
        if json_body is not None:
            kwargs["json"] = json_body
        try:
            client = (self.session or WorkerSession.current()).client()
            resp = getattr(client, method)(f"{self.runner_url.rstrip('/')}{path}", **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            named = rejected or failure
            raise click.ClickException(f"{self.verb}: {named} ({Problem(exc.response).detail or exc})") from exc
        except httpx.HTTPError as exc:
            raise click.ClickException(f"{self.verb}: {failure} ({exc})") from exc

    def _headers(self) -> dict[str, str]:
        headers = {LEASE_TOKEN_HEADER: self.lease_token} if self.lease_token else {}
        if self.traceparent:
            headers[TRACEPARENT_HEADER] = self.traceparent
        return headers
