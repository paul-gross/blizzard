"""The local runner daemon an operator verb reaches, and the door it reaches it through."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal

import click
import httpx

from blizzard.cli.param_rank import source_rank
from blizzard.runner.config import RunnerConfig

# A machine-local round trip (issue #43), so a hook-scale budget rather than the hub-client one.
LOCAL_CLIENT_TIMEOUT = 5.0


@dataclass(frozen=True)
class RunnerDaemon:
    """One operator verb's door onto the runner's local API — its UDS socket, or TCP when
    ``--runner-url`` names one. Never the store, and never the hub.

    A context manager: it closes the client and turns any transport failure inside the block
    into one ``verb: could not reach the runner at <where>``."""

    verb: str
    client: httpx.Client
    where: str

    @classmethod
    def reach(cls, verb: str, directory: str, runner_url: str | None) -> RunnerDaemon:
        """Ranked by where each value came from (``param_rank``) because ``--dir`` always *has*
        one: an explicit flag beats an ambient variable, and only a tie on the line is ambiguous."""
        ctx = click.get_current_context()
        dir_rank = source_rank(ctx.get_parameter_source("directory"))
        url_rank = source_rank(ctx.get_parameter_source("runner_url")) if runner_url is not None else -1

        if dir_rank == 2 and url_rank == 2:
            raise click.UsageError(
                "--dir and --runner-url are mutually exclusive: --dir names the socket, --runner-url TCP"
            )
        if url_rank > dir_rank and runner_url is not None:
            return cls(verb, httpx.Client(base_url=runner_url, timeout=LOCAL_CLIENT_TIMEOUT), runner_url)

        sock = RunnerConfig.socket_path_for(Path(directory))
        if not sock.exists():
            # No degraded read path — an absent socket is a daemon-not-running diagnostic,
            # never a reason to fall back to reading the store.
            raise click.ClickException(
                f"no runner daemon is serving at {sock} — start one with `blizzard runner host --dir {directory}`"
            )
        # The base_url host is a placeholder: the UDS transport decides where the bytes go.
        transport = httpx.HTTPTransport(uds=str(sock))
        client = httpx.Client(transport=transport, base_url="http://runner", timeout=LOCAL_CLIENT_TIMEOUT)
        return cls(verb, client, str(sock))

    def __enter__(self) -> RunnerDaemon:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> Literal[False]:
        """``Literal[False]``, not ``bool``: never swallowing is what keeps names bound after the block."""
        self.client.close()
        if isinstance(exc, httpx.HTTPError):
            raise self.unreachable(exc) from exc
        return False

    def get(self, path: str, *, params: dict[str, str] | None = None) -> httpx.Response:
        return self._call("get", path, params=params)

    def post(self, path: str, *, json_body: object | None = None) -> httpx.Response:
        return self._call("post", path, json_body=json_body)

    def patch(self, path: str, *, json_body: object | None = None) -> httpx.Response:
        return self._call("patch", path, json_body=json_body)

    def send(self, method: str, path: str, *, json_body: object | None = None) -> httpx.Response:
        kwargs: dict[str, object] = {}
        if json_body is not None:
            kwargs["json"] = json_body
        return getattr(self.client, method)(path, **kwargs)

    def unreachable(self, exc: Exception) -> click.ClickException:
        return click.ClickException(f"{self.verb}: could not reach the runner at {self.where} ({exc})")

    def _call(
        self, method: str, path: str, *, json_body: object | None = None, params: dict[str, str] | None = None
    ) -> httpx.Response:
        kwargs: dict[str, object] = {}
        if json_body is not None:
            kwargs["json"] = json_body
        if params is not None:
            kwargs["params"] = params
        resp = getattr(self.client, method)(path, **kwargs)
        resp.raise_for_status()
        return resp
