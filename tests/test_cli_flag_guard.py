"""A client verb or option held behind a flag: listed and marked in ``--help``, refused with the one message while
the addressed hub's flags answer has it off or lacks it, run once it is on (``bzh:flag-hides-behavior``)."""

from __future__ import annotations

from typing import Any

import click
import httpx2
import pytest

from blizzard.cli.collaborators import CliCollaborators
from blizzard.cli.flag_guard import check_declared
from blizzard.foundation.flags import FlagRegistry
from blizzard.hub.cli.collaborators import HubCliCollaborators
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.flag_gate import hub_flag_command, hub_flag_option
from blizzard.hub.cli.session_service import SessionService
from blizzard.wire.flags import FlagOff
from tests.session_store_fakes import InMemorySessionStore
from tests.worker_http import Bound, bind_stubs

pytestmark = pytest.mark.unit

_REGISTRY = FlagRegistry.parse(
    """
[flag.hub-thing]
owner = "epic:x"
summary = "A hub capability"
scope = "hub"
default = "off"

[flag.runner-thing]
owner = "epic:x"
summary = "A runner capability"
scope = "runner"
default = "off"
""",
    "fixture",
)


class _Response:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        pass


@click.group()
@click.pass_context
def _hub(ctx: click.Context) -> None:
    CliCollaborators.of(ctx)
    HubCliCollaborators.resolve(ctx, lambda: SessionService(InMemorySessionStore()))


@_hub.command("held", cls=FleetCommand)
@hub_flag_command("hub-thing", registry=_REGISTRY)
def _held(cli: CliContext) -> None:
    """Does the held thing."""
    click.echo("ran")


@_hub.command("plain", cls=FleetCommand)
@hub_flag_option("hub-thing", "--extra", "extra", registry=_REGISTRY, is_flag=True, help="Adds the extra.")
def _plain(cli: CliContext, extra: bool) -> None:
    """Does the plain thing."""
    click.echo(f"ran extra={extra}")


def _serve(answer: _Response) -> Bound:
    def get(url: str, **_: Any) -> _Response:
        assert url.endswith("/api/flags")
        return answer

    return bind_stubs(get=get)


def _flags(on: bool) -> _Response:
    return _Response(200, {"flags": {"hub-thing": on}, "overridden": []})


def _run(*args: str, bound: Bound | None = None) -> Any:
    return (bound or bind_stubs()).runner.invoke(_hub, list(args))


_REFUSAL = FlagOff.of("hub-thing").message


def test_a_held_verb_is_listed_and_marked_and_runs_nothing_for_help() -> None:
    assert "held" in _run("--help").output
    held = _run("held", "--help").output
    assert "Does the held thing." in held
    assert "Not yet on: this works only once the flag hub-thing is on." in held


@pytest.mark.parametrize("answer", [_flags(False), _Response(200, {"flags": {}, "overridden": []}), _Response(404, {})])
def test_a_held_verb_exits_1_with_the_message_while_off_or_unanswered(answer: _Response) -> None:
    result = _run("held", bound=_serve(answer))
    assert result.exit_code == 1
    assert _REFUSAL in result.output
    assert "ran" not in result.output.replace(_REFUSAL, "")


def test_a_held_verb_runs_once_its_flag_is_on() -> None:
    result = _run("held", bound=_serve(_flags(True)))
    assert (result.exit_code, result.output) == (0, "ran\n")


def test_a_held_option_is_listed_and_marked_refused_when_given_while_off_and_works_once_on() -> None:
    helped = _run("plain", "--help").output
    assert "--extra" in helped and "Not yet on:" in helped
    off = _serve(_flags(False))
    assert _run("plain", bound=off).output == "ran extra=False\n"
    refused = _run("plain", "--extra", bound=off)
    assert refused.exit_code == 1 and _REFUSAL in refused.output
    assert _run("plain", "--extra", bound=_serve(_flags(True))).output == "ran extra=True\n"


def test_a_flag_off_404_reaching_the_response_check_renders_the_same_message() -> None:
    body = {"detail": FlagOff.of("hub-thing").model_dump()}
    bound = bind_stubs(get=lambda url, **_: _Response(404, body))
    cli = CliContext(
        hub_url="http://hub.local",
        session_reader=InMemorySessionStore(),
        trace=_NoTrace(),
        client_factory=bound.collaborators.client_factory,
    )
    with pytest.raises(click.ClickException) as raised:
        cli.get("/api/held", "GET /held")
    assert raised.value.message == _REFUSAL


class _NoTrace:
    def headers(self) -> dict[str, str]:
        return {}


def test_a_declaration_naming_an_undeclared_flag_or_the_wrong_scope_is_refused() -> None:
    with pytest.raises(ValueError, match="not declared"):
        check_declared("mistyped", "hub", _REGISTRY)
    with pytest.raises(ValueError, match="does not answer"):
        check_declared("runner-thing", "hub", _REGISTRY)
    with pytest.raises(ValueError, match="does not answer"):
        hub_flag_command("runner-thing", registry=_REGISTRY)
    check_declared("hub-thing", "hub", _REGISTRY)


def test_a_flag_off_404_from_the_host_renders_the_same_message() -> None:
    from blizzard.runner.cli.daemon import RunnerDaemon

    body = {"detail": FlagOff.of("hub-thing").model_dump()}
    client = httpx2.Client(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(404, json=body)), base_url="http://r"
    )
    with pytest.raises(click.ClickException) as raised:
        RunnerDaemon("verb", client, "r").get_from_host("/api/held")
    assert raised.value.message == _REFUSAL
