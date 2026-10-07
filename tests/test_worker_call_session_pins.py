"""Branches of the worker call and its session that no other test pins: the exit code a run finishes
with, what finishing does with the client and the span post, and the hook's partial identities.
Unit tier (blizzard:unit-test)."""

from __future__ import annotations

from typing import Any

import click
import pytest

from blizzard.cli.collaborators import CliCollaborators
from blizzard.foundation import cli_spans
from blizzard.foundation.otlp_destination import TRACES_PATH
from blizzard.foundation.span_clock import Clock
from blizzard.runner.cli.worker_call import LEASE_TOKEN_HEADER, WorkerCall, WorkerSession
from tests.test_layering import _loaded_after_running
from tests.worker_http import StubClient

pytestmark = pytest.mark.unit

_PARENT = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"
_ENV = {
    "BLIZZARD_RUNNER_URL": "http://runner.local:8431/",
    "BLIZZARD_LEASE_ID": "lease_9",
    "BLIZZARD_TRACEPARENT": _PARENT,
}


def _collaborators(client: StubClient | None = None) -> CliCollaborators:
    """Collaborators whose client factory hands out ``client`` — a fresh stub when none is given."""
    handed = client or StubClient(None, None)
    return CliCollaborators(client_factory=lambda: handed, clock=Clock())  # type: ignore[arg-type,return-value]


def _runner_context() -> click.Context:
    """A click context the way the ``runner`` group leaves it: a worker session pinned to its root."""
    ctx = click.Context(click.Command("runner"))
    WorkerSession.begin(ctx, _collaborators())
    return ctx


def _record_sends(monkeypatch: pytest.MonkeyPatch, *, released: bool) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def send(client: object, url: str, span: object, **kwargs: Any) -> bool:
        sent.append({"client": client, "url": url, **kwargs})
        return released

    monkeypatch.setattr(cli_spans, "send", send)
    return sent


def _traced(environ: dict[str, str], collaborators: CliCollaborators | None = None) -> WorkerSession:
    session = WorkerSession(collaborators or _collaborators(), environ)
    session.open_span()
    return session


@pytest.mark.parametrize(
    ("raised", "code"),
    [
        (SystemExit(4), 4),
        (SystemExit(None), 0),
        (SystemExit("a message"), 1),
        (ValueError("boom"), 1),
        (click.ClickException("x"), 1),
        (click.UsageError("x"), 2),
    ],
)
def test_run_finishes_the_session_with_the_exit_code_the_exception_ends_in(
    monkeypatch: pytest.MonkeyPatch, raised: BaseException, code: int
) -> None:
    session = WorkerSession(_collaborators(), {})
    finished: list[int] = []
    monkeypatch.setattr(session, "finish", finished.append)

    def invoke() -> object:
        raise raised

    with pytest.raises(type(raised)):
        session.run(invoke)

    assert finished == [code]


def test_run_finishes_with_zero_and_returns_the_result_of_a_command_that_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = WorkerSession(_collaborators(), {})
    finished: list[int] = []
    monkeypatch.setattr(session, "finish", finished.append)

    assert session.run(lambda: "done") == "done"
    assert finished == [0]


def test_an_abandoned_span_post_leaves_the_client_open(monkeypatch: pytest.MonkeyPatch) -> None:
    client = StubClient(None, None)
    _record_sends(monkeypatch, released=False)

    _traced(_ENV, _collaborators(client)).finish(0)

    assert not client.closed


def test_a_finished_span_post_closes_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    client = StubClient(None, None)
    sent = _record_sends(monkeypatch, released=True)

    _traced(_ENV, _collaborators(client)).finish(0)

    assert client.closed
    assert sent[0]["client"] is client


def test_an_untraced_session_closes_the_client_it_built_and_posts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    client = StubClient(None, None)
    sent = _record_sends(monkeypatch, released=True)
    session = WorkerSession(_collaborators(client), {})
    session.client()

    session.finish(0)

    assert client.closed
    assert sent == []


def test_a_session_that_never_built_a_client_finishes_quietly(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _record_sends(monkeypatch, released=True)

    WorkerSession(_collaborators(), {}).finish(0)

    assert sent == []


def test_the_span_post_goes_to_the_runner_traces_path_with_the_lease_token(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _record_sends(monkeypatch, released=True)

    _traced({**_ENV, "BLIZZARD_LEASE_TOKEN": "tok"}).finish(0)

    assert sent[0]["url"] == f"http://runner.local:8431{TRACES_PATH}"
    assert sent[0]["headers"] == {LEASE_TOKEN_HEADER: "tok"}


def test_a_missing_lease_token_omits_the_header(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _record_sends(monkeypatch, released=True)

    _traced(_ENV).finish(0)

    assert sent[0]["headers"] == {}


def test_the_sessions_own_environment_is_handed_to_the_send_for_its_debug_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _record_sends(monkeypatch, released=True)
    environ = {**_ENV, "BLIZZARD_TRACE_DEBUG": "1"}

    _traced(environ).finish(0)

    assert sent[0]["environ"] is not None
    assert sent[0]["environ"]["BLIZZARD_TRACE_DEBUG"] == "1"


def test_finishing_the_span_records_the_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _record_sends(monkeypatch, released=True)
    session = _traced(_ENV)
    assert session._span is not None
    codes: list[int] = []
    monkeypatch.setattr(session._span, "finish", codes.append)

    session.finish(7)

    assert codes == [7]
    assert len(sent) == 1


@pytest.mark.parametrize(
    "present",
    [
        {"BLIZZARD_LEASE_ID": "lease_9"},
        {"BLIZZARD_RUNNER_URL": "http://runner.local:8431"},
        {},
    ],
    ids=["no-runner-url", "no-lease-id", "neither"],
)
def test_a_hook_missing_either_half_of_its_identity_skips_and_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], present: dict[str, str]
) -> None:
    for key in ("BLIZZARD_LEASE_ID", "BLIZZARD_RUNNER_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in present.items():
        monkeypatch.setenv(key, value)

    assert WorkerCall.hook("heartbeat") is None
    assert "heartbeat: no BLIZZARD_LEASE_ID/BLIZZARD_RUNNER_URL in the environment; skipping" in capsys.readouterr().err


def test_a_hook_with_both_halves_is_the_bound_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLIZZARD_LEASE_ID", "lease_9")
    monkeypatch.setenv("BLIZZARD_RUNNER_URL", "http://runner.local:8431")

    with _runner_context():
        call = WorkerCall.hook("heartbeat", traced=False)

    assert call is not None
    assert (call.verb, call.runner_url, call.lease_id) == ("heartbeat", "http://runner.local:8431", "lease_9")


def test_a_worker_call_verb_under_a_sampled_traceparent_loads_no_opentelemetry() -> None:
    """The span a call-making verb posts must not pull in the OpenTelemetry SDK either."""
    loaded = _loaded_after_running(
        ["runner", "artifact", "list"],
        {
            "BLIZZARD_TRACEPARENT": _PARENT,
            "BLIZZARD_RUNNER_URL": "http://127.0.0.1:1",
            "BLIZZARD_LEASE_ID": "lease_9",
            "BLIZZARD_LEASE_TOKEN": "tok",
        },
    )

    heavy = sorted(m for m in loaded if m.split(".")[0] == "opentelemetry")
    assert not heavy, f"a call-making worker verb loaded {len(heavy)} opentelemetry modules, first {heavy[:3]}"
    assert "blizzard.foundation.cli_spans" in loaded


def test_a_worker_call_outside_a_runner_invocation_fails_rather_than_building_a_session() -> None:
    with pytest.raises(RuntimeError, match="no worker session"):
        WorkerSession.current()

    with click.Context(click.Command("probe")), pytest.raises(RuntimeError, match="no worker session"):
        WorkerSession.current()
