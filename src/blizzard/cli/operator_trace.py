"""The root span an operator's ``blizzard hub`` / ``blizzard runner`` command records.

The operator counterpart of ``WorkerSession``: with an OTLP endpoint configured and no worker identity,
the command is one span, its ``traceparent`` rides its requests, and it is posted as the command ends.
Nothing of the emitter loads until a span exists. Contract:
``blizzard-product:/plans/tracing/platform-spans/spec/instrumentation.md``."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

import click

from blizzard.cli.lazy_group import LazyGroup
from blizzard.foundation.trace_export.settings import configured_endpoint, export_switched_off

if TYPE_CHECKING:
    import httpx

    from blizzard.foundation.cli_spans import CliSpan
    from blizzard.foundation.otlp_destination import OtlpDestination

# A worker's spawn environment: it traces through its runner, never to an operator's endpoint.
ENV_TRACEPARENT = "BLIZZARD_TRACEPARENT"
ENV_RUNNER_URL = "BLIZZARD_RUNNER_URL"
SERVICE_DEFAULT = "blizzard-cli"
TRACEPARENT_HEADER = "traceparent"

# `host` becomes a daemon with its own SDK pipeline; `record-marker` is run by a hub step, not an operator.
_NEVER_TRACED = frozenset({"host", "record-marker"})
_META_KEY = "blizzard.cli.operator_trace"


def _short_lived_client() -> httpx.Client:
    import httpx

    return httpx.Client()


# The seam a test replaces to hand the finishing send a client over a canned transport.
client_factory: Callable[[], httpx.Client] = _short_lived_client


def exit_code_of(exc: BaseException) -> int:
    """The process exit code a command's exception ends in, as click's ``main`` would turn it."""
    if isinstance(exc, (click.exceptions.Exit, click.ClickException)):
        return exc.exit_code
    if isinstance(exc, SystemExit):
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    return 1


class OperatorTrace:
    """One operator command's state: the destination it will post to, and its root span once the
    command's own name is known. The ``hub`` and ``runner`` groups build and finish it."""

    def __init__(self, root: str, environ: Mapping[str, str] | None = None) -> None:
        self._root = root
        self._environ = os.environ if environ is None else environ
        self._destination = self._resolve_destination()
        self._span: CliSpan | None = None

    @classmethod
    def begin(cls, ctx: click.Context, root: str) -> OperatorTrace:
        trace = cls(root)
        ctx.find_root().meta[_META_KEY] = trace
        return trace

    @classmethod
    def of(cls, ctx: click.Context) -> OperatorTrace | None:
        return ctx.find_root().meta.get(_META_KEY)

    @classmethod
    def headers(cls) -> dict[str, str]:
        """The ``traceparent`` a request of the running command carries — empty when untraced."""
        ctx = click.get_current_context(silent=True)
        trace = cls.of(ctx) if ctx is not None else None
        if trace is None or trace._span is None:
            return {}
        return {TRACEPARENT_HEADER: trace._span.traceparent}

    def _resolve_destination(self) -> OtlpDestination | None:
        """The endpoint to post to, only when this is an operator: not a worker's environment, and
        an endpoint configured and not switched off. The cheap checks come first so an untraced
        command never imports the resolver."""
        if self._environ.get(ENV_TRACEPARENT) or self._environ.get(ENV_RUNNER_URL):
            return None
        if not configured_endpoint(self._environ) or export_switched_off(self._environ):
            return None
        from blizzard.foundation.otlp_destination import OtlpDestination

        return OtlpDestination.of(self._environ)

    def command_resolved(self, group: click.Group, ctx: click.Context, args: list[str]) -> None:
        """Open the root span once the command is known — before its body runs — unless it is one
        that is never traced."""
        if self._destination is None or self._span is not None:
            return
        names = self._names(group, ctx, args)
        if not names:
            return
        if names[0] in _NEVER_TRACED:
            self._destination = None
            return
        from blizzard.foundation import cli_spans

        self._span = cli_spans.CliSpan.root(" ".join([self._root, *names]), service_name=_service_name(self._environ))

    @staticmethod
    def _names(group: click.Group, ctx: click.Context, args: list[str]) -> list[str]:
        """The command names down to the leaf — only names that resolve, never an option or a value."""
        names: list[str] = []
        current: click.Command = group
        for arg in args:
            if not isinstance(current, click.Group) or arg.startswith("-"):
                break
            command = current.get_command(ctx, arg)
            if command is None:
                break
            names.append(arg)
            current = command
        return names

    def run(self, invoke: Callable[[], object]) -> object:
        """Run a command, then finish the trace with the exit code it ends in."""
        code = 0
        try:
            return invoke()
        except BaseException as exc:
            code = exit_code_of(exc)
            raise
        finally:
            self.finish(code)

    def finish(self, exit_code: int) -> None:
        """Send the span if one opened, then close the client that carried it."""
        span, destination = self._span, self._destination
        self._span = None
        if span is None or destination is None:
            return
        from blizzard.foundation import cli_spans

        span.finish(exit_code)
        client = client_factory()
        if cli_spans.send(
            client,
            destination.url,
            span,
            headers=destination.headers,
            cap=cli_spans.OPERATOR_SEND_CAP_SECONDS,
            environ=self._environ,
        ):
            client.close()


def _service_name(environ: Mapping[str, str]) -> str:
    from blizzard.foundation.trace_attributes import service_name

    return service_name(environ, SERVICE_DEFAULT)


class OperatorGroup(LazyGroup):
    """A lazy command registry whose commands are operator-traced: the root of ``hub`` or ``runner``.

    The trace finishes — and the span is sent — as the command ends, before click reports any error."""

    def __init__(self, *args: Any, trace_root: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._trace_root = trace_root

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        trace = OperatorTrace.of(ctx)
        if trace is not None:
            trace.command_resolved(self, ctx, args)
        return super().resolve_command(ctx, args)

    def invoke(self, ctx: click.Context) -> object:
        return OperatorTrace.begin(ctx, self._trace_root).run(lambda: super(OperatorGroup, self).invoke(ctx))
