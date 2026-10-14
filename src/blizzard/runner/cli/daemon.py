"""The local runner daemon an operator verb reaches, and the door it reaches it through."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Literal

import click
import httpx

from blizzard.cli.operator_trace import ITraceHeaders
from blizzard.cli.param_rank import ParamSource
from blizzard.foundation.runner_paths import runner_prefix
from blizzard.runner.host_layout import HostLayout

# A machine-local round trip, so a hook-scale budget rather than the hub-client one.
LOCAL_CLIENT_TIMEOUT = 5.0


def uds_client(sock: Path, trace: ITraceHeaders) -> httpx.Client:
    """A client whose transport reaches the runner over ``sock`` — the base_url host is a
    placeholder, since the UDS transport decides where the bytes go."""
    transport = httpx.HTTPTransport(uds=str(sock))
    return httpx.Client(
        transport=transport,
        base_url="http://runner",
        timeout=LOCAL_CLIENT_TIMEOUT,
        headers=trace.headers(),
    )


@dataclass(frozen=True)
class RunnerDaemon:
    """One operator verb's door onto the runner's local API — its UDS socket, or TCP when
    an operator-supplied override names one. Never the store, and never the hub.

    A context manager: it closes the client and turns any transport failure inside the block
    into one ``verb: could not reach the runner at <where>``."""

    verb: str
    client: httpx.Client
    where: str
    #: The runner this door reaches, once the host has named it.
    _runner: dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def reach(cls, verb: str, directory: str, runner_url: str | None, trace: ITraceHeaders) -> RunnerDaemon:
        """Ranked by where each value came from, see ``src/blizzard/cli/param_rank.py`` —
        only a tie on the command line between the two is ambiguous."""
        dir_source = ParamSource.of("directory")
        url_source = ParamSource.of("runner_url") if runner_url is not None else None

        if dir_source.on_commandline and url_source is not None and url_source.on_commandline:
            raise click.UsageError(
                "--dir and --runner-url are mutually exclusive: --dir names the socket, --runner-url TCP"
            )
        if url_source is not None and url_source > dir_source and runner_url is not None:
            client = httpx.Client(base_url=runner_url, timeout=LOCAL_CLIENT_TIMEOUT, headers=trace.headers())
            return cls(verb, client, runner_url)

        sock = HostLayout(Path(directory)).socket_path
        if not sock.exists():
            # No degraded read path — an absent socket is a daemon-not-running diagnostic,
            # never a reason to fall back to reading the store.
            raise click.ClickException(
                f"no runner daemon is serving at {sock} — start one with `blizzard runner host --dir {directory}`"
            )
        return cls(verb, uds_client(sock, trace), str(sock))

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
        """``GET`` the runner's own ``path``, raising on a non-2xx answer."""
        return self._call("get", self.runner_path(path), params=params)

    def post(self, path: str, *, json_body: object | None = None) -> httpx.Response:
        return self._call("post", self.runner_path(path), json_body=json_body)

    def patch(self, path: str, *, json_body: object | None = None) -> httpx.Response:
        return self._call("patch", self.runner_path(path), json_body=json_body)

    def send(self, method: str, path: str, *, json_body: object | None = None) -> httpx.Response:
        """``method`` on the runner's own ``path``, whatever it answers."""
        return self.send_to_host(method, self.runner_path(path), json_body=json_body)

    def send_to_host(self, method: str, path: str, *, json_body: object | None = None) -> httpx.Response:
        """``method`` on the host's own ``path``, at its root, whatever it answers."""
        kwargs: dict[str, object] = {}
        if json_body is not None:
            kwargs["json"] = json_body
        return getattr(self.client, method)(path, **kwargs)

    def get_from_host(self, path: str) -> httpx.Response:
        """``GET`` the host's own ``path``, at its root, raising on a non-2xx answer."""
        return self._call("get", path)

    def runner_path(self, path: str) -> str:
        """``path`` under the prefix of the runner this door reaches."""
        return runner_prefix(self.runner_id()) + path

    def runner_id(self) -> str:
        """The id of the one runner the host serves, read from its runner list once. A host serving none fails naming
        why each is halted; one serving several fails naming them."""
        if "id" not in self._runner:
            self._runner["id"] = self._sole_runner_id()
        return self._runner["id"]

    def _sole_runner_id(self) -> str:
        items: list[dict[str, object]] = self._call("get", "/api/host/runners").json()["items"]
        serving = [item for item in items if item["state"] == "serving"]
        if len(serving) == 1:
            return str(serving[0]["runner_id"])
        if not serving:
            halted = "; ".join(
                f"runner {item['name']} ({item['runner_id'] or 'no id'}) is halted: "
                f"{'; '.join(map(str, item['halted_reasons']))}"  # type: ignore[arg-type]
                for item in items
            )
            raise click.ClickException(f"{self.verb}: the host at {self.where} serves no runner: {halted or 'none'}")
        names = ", ".join(f"{item['name']} ({item['runner_id']})" for item in serving)
        raise click.ClickException(f"{self.verb}: the host at {self.where} serves several runners: {names}")

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


def daemon_holding(sock: Path, trace: ITraceHeaders) -> str | None:
    """What is holding the socket ``sock``, or ``None`` when nothing is — the single-owner guard's
    probe for a verb that writes or moves the store. Fail-closed: a socket that does not answer
    its health probe cleanly is still held."""
    if not sock.exists():
        return None
    try:
        with uds_client(sock, trace) as client:
            response = client.get("/api/health")
    except httpx.ConnectError:
        return None  # a socket file an ungraceful exit left behind — nothing is listening on the corpse
    except httpx.HTTPError as exc:
        return f"something holds {sock} but did not answer its health probe ({exc})"
    if response.status_code != 200:
        return f"something holds {sock} and answered its health probe {response.status_code}"
    return f"a runner daemon is serving at {sock}"
