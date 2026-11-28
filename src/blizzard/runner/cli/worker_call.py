"""The identity a spawned worker acts under, and the calls it makes with it."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

import click

from blizzard.cli.collaborators import CliCollaborators, Client, ClientFactory, HTTPError, HTTPStatusError, Response
from blizzard.cli.operator_trace import ITraceHeaders, exit_code_of
from blizzard.cli.refusal import refusal_of
from blizzard.foundation.roles import dto
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


@dto
@dataclass(frozen=True)
class WorkerPolicy:
    """Identity and tracing requirements for one worker operation."""

    verb: str
    lease: bool = True
    soft: bool = False
    traced: bool = True
    skip_elicitation: bool = False


class WorkerSession:
    """One worker command's state: the one HTTP client every request and the span post share,
    and the command's span when a trace context was handed down."""

    def __init__(
        self, collaborators: CliCollaborators, environ: Mapping[str, str] | None = None, *, group_path: str = ""
    ) -> None:
        self._collaborators = collaborators
        self._environ = os.environ if environ is None else environ
        self._group_path = group_path
        self._client: Client | None = None
        self._span: CliSpan | None = None
        self._parent = self._traced_parent()

    @classmethod
    def from_context(cls, ctx: click.Context) -> WorkerSession:
        session = ctx.find_root().meta.get(_SESSION_META_KEY)
        if session is None:
            return cls._missing()
        return session

    @staticmethod
    def _missing() -> WorkerSession:
        raise RuntimeError("no worker session: a worker call must run inside a `blizzard runner` invocation")

    @classmethod
    def begin(
        cls, ctx: click.Context, collaborators: CliCollaborators, environ: Mapping[str, str] | None = None
    ) -> WorkerSession:
        session = cls(collaborators, environ, group_path=ctx.command_path)
        ctx.find_root().meta[_SESSION_META_KEY] = session
        return session

    @property
    def environ(self) -> Mapping[str, str]:
        """The environment this command reads its identity and settings from — the one place a worker verb does."""
        return self._environ

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

    def client(self, factory: ClientFactory | None = None) -> Client:
        if self._client is None:
            self._client = (factory or self._collaborators.client_factory)()
        return self._client

    def headers(self) -> dict[str, str]:
        return {TRACEPARENT_HEADER: self._span.traceparent} if self._span is not None else {}

    @property
    def span(self) -> CliSpan | None:
        """This command's span, once :meth:`open_span` has started it."""
        return self._span

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
                clock=self._collaborators.clock,
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
            from blizzard.foundation.otlp_destination import TRACES_PATH

            span.finish(exit_code)
            token = self._environ.get(ENV_LEASE_TOKEN)
            released = cli_spans.send(
                client,
                f"{self._environ.get(ENV_RUNNER_URL, '').rstrip('/')}{TRACES_PATH}",
                span,
                headers={LEASE_TOKEN_HEADER: token} if token else {},
                cap=cli_spans.worker_send_cap(self._environ),
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
            code = exit_code_of(exc)
            raise
        finally:
            self.finish(code)


@dataclass(frozen=True)
class WorkerCall:
    """A spawned worker's ambient identity — the runner it reports to, and the lease it acts
    under (``""`` for a verb that names its own chunk instead)."""

    verb: str
    runner_url: str
    lease_id: str
    lease_token: str | None
    session: WorkerSession
    traceparent: str | None
    client_factory: ClientFactory | None = None
    trace_headers: ITraceHeaders | None = None

    @classmethod
    def select(
        cls,
        session: WorkerSession,
        policy: WorkerPolicy,
        *,
        client_factory: ClientFactory | None = None,
        trace_headers: ITraceHeaders | None = None,
    ) -> WorkerCall | None:
        """Bind an operation's policy to the group-owned session."""
        environ = session.environ
        if policy.skip_elicitation and environ.get(ENV_ELICITATION):
            return None
        runner_url = environ.get(ENV_RUNNER_URL)
        lease_id = environ.get(ENV_LEASE_ID)
        if not runner_url or (policy.lease and not lease_id):
            wanted = f"{ENV_LEASE_ID}/{ENV_RUNNER_URL}" if policy.lease else ENV_RUNNER_URL
            message = f"{policy.verb}: no {wanted} in the environment"
            if policy.soft:
                click.echo(f"{message}; skipping", err=True)
                return None
            raise click.ClickException(message)
        return cls(
            policy.verb,
            runner_url,
            lease_id or "",
            environ.get(ENV_LEASE_TOKEN),
            session,
            session.open_span() if policy.traced else None,
            client_factory,
            trace_headers,
        )

    def leased(self, suffix: str) -> str:
        return f"/api/leases/{self.lease_id}/{suffix}"

    def get(
        self, path: str, *, failure: str, rejected: str | None = None, params: dict[str, str] | None = None
    ) -> Response:
        return self._call("get", path, failure=failure, rejected=rejected, params=params, timeout=READ_TIMEOUT)

    def post(
        self, path: str, *, failure: str, rejected: str | None = None, json_body: object | None = None
    ) -> Response:
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
    ) -> Response:
        """One call, with this lease's token attached and a failure named as ``verb: failure``.

        A rejection the worker can act on carries its guidance in the body, so that is preferred
        over the bare status line; ``rejected`` names it differently from an unreachable runner
        where that helps."""
        kwargs: dict[str, object] = {"timeout": timeout, "headers": self._headers(), "params": params}
        if json_body is not None:
            kwargs["json"] = json_body
        try:
            client = self.session.client(self.client_factory)
            resp = getattr(client, method)(f"{self.runner_url.rstrip('/')}{path}", **kwargs)
            resp.raise_for_status()
            return resp
        except HTTPStatusError as exc:
            named = rejected or failure
            raise click.ClickException(f"{self.verb}: {named} ({refusal_of(exc.response).message or exc})") from exc
        except HTTPError as exc:
            raise click.ClickException(f"{self.verb}: {failure} ({exc})") from exc

    def _headers(self) -> dict[str, str]:
        headers = {LEASE_TOKEN_HEADER: self.lease_token} if self.lease_token else {}
        if self.traceparent:
            headers.update(
                self.trace_headers.headers() if self.trace_headers else {TRACEPARENT_HEADER: self.traceparent}
            )
        return headers
