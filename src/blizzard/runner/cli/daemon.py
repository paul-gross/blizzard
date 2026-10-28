"""The local runner daemon an operator verb reaches, and the door it reaches it through."""

from __future__ import annotations

from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, cast

import click

from blizzard.cli.collaborators import CliCollaborators, Client, ClientFactory, ConnectError, HTTPError, Response
from blizzard.cli.operator_trace import ITraceHeaders, OperatorTrace
from blizzard.cli.param_rank import ParamSource
from blizzard.foundation.roles import collaborator
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.route_paths import runner_prefix

# A machine-local round trip, so a hook-scale budget rather than the hub-client one.
LOCAL_CLIENT_TIMEOUT = 5.0


@dataclass(frozen=True)
class Door:
    """What a verb reaches the runner with: the trace its requests carry and the factory its clients come from."""

    trace: ITraceHeaders
    client_factory: ClientFactory

    @classmethod
    def of(cls, ctx: click.Context) -> Door:
        return cls(OperatorTrace.source(ctx), CliCollaborators.of(ctx).client_factory)

    def uds_client(self, sock: Path) -> Client:
        """A client reaching the runner over ``sock``; the base_url host is a placeholder."""
        return self.client_factory(
            base_url="http://runner", timeout=LOCAL_CLIENT_TIMEOUT, headers=self.trace.headers(), uds=str(sock)
        )


def runner_option(command: Any) -> Any:
    """``--runner``: which of the host's runners a verb addresses, by name or id."""
    return click.option(
        "--runner",
        "runner",
        default=None,
        help="The host's runner to address, by name or id; may be omitted on a host of one runner.",
    )(command)


@collaborator
@dataclass(frozen=True)
class RunnerDaemon:
    """One operator verb's door onto the runner's local API — its UDS socket, or TCP when
    an operator-supplied override names one. Never the store, and never the hub.

    A context manager: it closes the client and turns any transport failure inside the block
    into one ``verb: could not reach the runner at <where>``."""

    verb: str
    client: Client
    where: str
    #: The runner the operator named with ``--runner``, by name or id; ``None`` lets a host of one answer.
    selector: str | None = None
    #: The runner this door reaches, once the host has named it.
    _runner: dict[str, str] = field(default_factory=dict[str, str], compare=False, repr=False)

    @classmethod
    def reach(
        cls,
        verb: str,
        directory: str,
        runner_url: str | None,
        door: Door,
        runner: str | None = None,
    ) -> RunnerDaemon:
        """Ranked by where each value came from, see ``src/blizzard/cli/param_rank.py`` —
        only a tie on the command line between the two is ambiguous."""
        dir_source = ParamSource.of("directory")
        url_source = ParamSource.of("runner_url") if runner_url is not None else None

        if dir_source.on_commandline and url_source is not None and url_source.on_commandline:
            raise click.UsageError(
                "--dir and --runner-url are mutually exclusive: --dir names the socket, --runner-url TCP"
            )
        if url_source is not None and url_source > dir_source and runner_url is not None:
            client = door.client_factory(
                base_url=runner_url, timeout=LOCAL_CLIENT_TIMEOUT, headers=door.trace.headers()
            )
            return cls(verb, client, runner_url, runner)

        sock = HostLayout(Path(directory)).socket_path
        if not sock.exists():
            # No degraded read path — an absent socket is a daemon-not-running diagnostic,
            # never a reason to fall back to reading the store.
            raise click.ClickException(
                f"no runner daemon is serving at {sock} — start one with `blizzard runner host --dir {directory}`"
            )
        return cls(verb, door.uds_client(sock), str(sock), runner)

    def __enter__(self) -> RunnerDaemon:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> Literal[False]:
        """``Literal[False]``, not ``bool``: never swallowing is what keeps names bound after the block."""
        self.client.close()
        if isinstance(exc, HTTPError):
            raise self.unreachable(exc) from exc
        return False

    def get(self, path: str, *, params: dict[str, str] | None = None) -> Response:
        """``GET`` the runner's own ``path``, raising on a non-2xx answer."""
        return self._call("get", self.runner_path(path), params=params)

    def post(self, path: str, *, json_body: object | None = None) -> Response:
        return self._call("post", self.runner_path(path), json_body=json_body)

    def patch(self, path: str, *, json_body: object | None = None) -> Response:
        return self._call("patch", self.runner_path(path), json_body=json_body)

    def send(self, method: str, path: str, *, json_body: object | None = None) -> Response:
        """``method`` on the runner's own ``path``, whatever it answers."""
        return self.send_to_host(method, self.runner_path(path), json_body=json_body)

    def send_to_host(self, method: str, path: str, *, json_body: object | None = None) -> Response:
        """``method`` on the host's own ``path``, at its root, whatever it answers."""
        kwargs: dict[str, object] = {}
        if json_body is not None:
            kwargs["json"] = json_body
        return getattr(self.client, method)(path, **kwargs)

    def get_from_host(self, path: str) -> Response:
        """``GET`` the host's own ``path``, at its root, raising on a non-2xx answer."""
        return self._call("get", path)

    def runner_path(self, path: str) -> str:
        """``path`` under the prefix of the runner this door reaches."""
        return runner_prefix(self.runner_id()) + path

    def runner_id(self) -> str:
        """The id of the runner this door addresses, resolved against the host's runner list once: the one ``--runner``
        names by name or id, or the host's only runner when it is omitted. Fails naming the host's runners when the
        selector matches none or, omitted, the host holds several; fails naming why when the runner is halted."""
        if "id" not in self._runner:
            self._runner["id"] = self._resolve_runner_id()
        return self._runner["id"]

    def _resolve_runner_id(self) -> str:
        items: list[dict[str, Any]] = self._call("get", "/api/host/runners").json()["items"]
        if self.selector is not None:
            matched = [item for item in items if self.selector in (item["name"], item["runner_id"])]
            if not matched:
                raise click.ClickException(
                    f"{self.verb}: the host at {self.where} holds no runner {self.selector!r}: {_named(items)}"
                )
            if len(matched) > 1:
                raise click.ClickException(
                    f"{self.verb}: --runner {self.selector!r} matches several runners on the host at "
                    f"{self.where}, so name one by id: {_named(matched)}"
                )
            chosen = matched[0]
        elif len(items) == 1:
            chosen = items[0]
        elif items:
            raise click.ClickException(
                f"{self.verb}: the host at {self.where} holds several runners, so name one with --runner: "
                f"{_named(items)}"
            )
        else:
            raise click.ClickException(f"{self.verb}: the host at {self.where} serves no runner: none")
        # A runner its hub halts is still served, and its own views — the status naming why — still read.
        if chosen["state"] != "serving" and not chosen.get("served", False):
            serves_none = all(item["state"] != "serving" and not item.get("served", False) for item in items)
            raise click.ClickException(
                f"{self.verb}: the host at {self.where} {'serves no runner: ' if serves_none else 'holds '}"
                f"runner {chosen['name']} "
                f"({chosen['runner_id'] or 'no id'}) is halted: {'; '.join(map(str, chosen['halted_reasons']))}"
            )
        return str(chosen["runner_id"])

    def unreachable(self, exc: Exception) -> click.ClickException:
        return click.ClickException(f"{self.verb}: could not reach the runner at {self.where} ({exc})")

    def _call(
        self, method: str, path: str, *, json_body: object | None = None, params: dict[str, str] | None = None
    ) -> Response:
        kwargs: dict[str, object] = {}
        if json_body is not None:
            kwargs["json"] = json_body
        if params is not None:
            kwargs["params"] = params
        resp = getattr(self.client, method)(path, **kwargs)
        if resp.status_code == HTTPStatus.NOT_FOUND and _flag_off(resp):
            raise click.ClickException(resp.json()["detail"]["message"])
        resp.raise_for_status()
        return resp


def _flag_off(resp: Response) -> bool:
    """Whether a ``404`` is the host's ``flag_off`` refusal: the route is held behind a flag that is off."""
    try:
        body: object = resp.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    body_dict = cast(dict[object, object], body)
    detail = body_dict.get("detail")
    if not isinstance(detail, dict):
        return False
    detail_dict = cast(dict[object, object], detail)
    return detail_dict.get("code") == "flag_off" and isinstance(detail_dict.get("message"), str)


def _named(items: list[dict[str, Any]]) -> str:
    return ", ".join(f"{item['name']} ({item['runner_id'] or 'no id'}, {item['state']})" for item in items) or "none"


def daemon_holding(sock: Path, door: Door) -> str | None:
    """What is holding the socket ``sock``, or ``None`` when nothing is — the single-owner guard's
    probe for a verb that writes or moves the store. Fail-closed: a socket that does not answer
    its health probe cleanly is still held."""
    if not sock.exists():
        return None
    try:
        with door.uds_client(sock) as client:
            response = client.get("/api/health")
    except ConnectError:
        return None  # a socket file an ungraceful exit left behind — nothing is listening on the corpse
    except HTTPError as exc:
        return f"something holds {sock} but did not answer its health probe ({exc})"
    if response.status_code != 200:
        return f"something holds {sock} and answered its health probe {response.status_code}"
    return f"a runner daemon is serving at {sock}"
