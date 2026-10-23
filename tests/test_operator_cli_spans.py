"""An operator's ``blizzard hub`` / ``blizzard runner`` command is one root span — its context
injected, its exit code recorded, its send capped — and none of it changes the command's output or
exit code. Unit tier (blizzard:unit-test)."""

from __future__ import annotations

import inspect
import json
import socket
import time
from pathlib import Path

import click
import httpx2
import pytest
from click.testing import Result

from blizzard.cli import operator_trace
from blizzard.cli.collaborators import CliCollaborators
from blizzard.foundation import cli_spans
from blizzard.foundation.otlp_destination import OtlpDestination, parse_headers
from blizzard.foundation.span_clock import Clock
from blizzard.foundation.trace_ids import parse_traceparent
from blizzard.hub.cli import hub as hub_group
from blizzard.runner.cli import external_usage
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.cli.daemon import Door
from tests.worker_http import Bound, bind_transport

pytestmark = pytest.mark.unit

_ENDPOINT = "http://collector.local:4318"
_OPERATOR_ENV = {"OTEL_EXPORTER_OTLP_ENDPOINT": _ENDPOINT, "BZ_HUB_URL": "http://hub.local:8421"}
_PLANTED = "planted-chunk-id"


class _Collector:
    """The span post's far end: records every request it is handed."""

    def __init__(self, status: int = 200) -> None:
        self.requests: list[httpx2.Request] = []
        self.status = status

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status)

    def spans(self) -> list[dict]:
        return [json.loads(r.content)["resourceSpans"][0]["scopeSpans"][0]["spans"][0] for r in self.requests]


class _HubCalls:
    """The hub's far end: records the headers of each request and answers an empty chunk list."""

    def __init__(self, status: int = 200) -> None:
        self.headers: list[dict[str, str]] = []
        self.status = status

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.headers.append(dict(request.headers))
        return httpx2.Response(self.status, json={"chunks": [], "next_cursor": None}, request=request)


def _bind(
    collector: _Collector | None = None, hub: _HubCalls | None = None, *, reach_the_network: bool = False
) -> Bound:
    """Collaborators answering the collector and the hub from the doubles given; any other host is refused."""

    def route(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "collector.local" and collector is not None:
            return collector(request)
        if request.url.host == "hub.local" and hub is not None:
            return hub(request)
        if reach_the_network:
            return httpx2.HTTPTransport().handle_request(request)
        raise AssertionError(f"unexpected request to {request.url}")

    return bind_transport(route)


def _hub(bound: Bound, argv: list[str], env: dict[str, str] | None = None) -> Result:
    return bound.runner.invoke(hub_group, argv, env={**_OPERATOR_ENV, **(env or {})})


def test_a_hub_command_is_one_root_span_its_context_injected() -> None:
    collector, hub = _Collector(), _HubCalls()
    bound = _bind(collector, hub)

    result = _hub(bound, ["chunk", "list"])

    assert result.exit_code == 0, result.output
    [span] = collector.spans()
    assert span["name"] == "hub chunk list"
    assert "parentSpanId" not in span
    assert span["flags"] == 1
    [injected] = [parse_traceparent(h["traceparent"]) for h in hub.headers]
    assert injected is not None
    assert (f"{injected.trace_id:032x}", f"{injected.span_id:016x}") == (span["traceId"], span["spanId"])
    assert collector.requests[0].url == f"{_ENDPOINT}/v1/traces"
    assert "traceparent" not in collector.requests[0].headers


def test_the_payload_names_the_command_and_exit_code_and_carries_no_argument() -> None:
    collector = _Collector()
    bound = _bind(collector, _HubCalls(status=500))

    result = _hub(bound, ["chunk", "show", _PLANTED])

    assert result.exit_code == 1
    body = collector.requests[0].content.decode()
    assert _PLANTED not in body
    payload = json.loads(body)["resourceSpans"][0]
    span = payload["scopeSpans"][0]["spans"][0]
    assert span["name"] == "hub chunk show"
    assert {"key": "process.exit.code", "value": {"intValue": "1"}} in span["attributes"]
    assert span["status"] == {"code": 2}
    assert payload["scopeSpans"][0]["scope"] == {"name": "blizzard.cli", "version": "1"}
    assert payload["resource"]["attributes"] == [{"key": "service.name", "value": {"stringValue": "blizzard-cli"}}]


def test_the_configured_service_name_and_headers_reach_the_send() -> None:
    collector = _Collector()
    bound = _bind(collector, _HubCalls())

    _hub(
        bound,
        ["chunk", "list"],
        {
            "OTEL_SERVICE_NAME": "ops-laptop",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://collector.local:9/custom",
            "OTEL_EXPORTER_OTLP_HEADERS": "authorization=Bearer%20abc, x-team=blue",
        },
    )

    [request] = collector.requests
    assert str(request.url) == "http://collector.local:9/custom"
    assert request.headers["authorization"] == "Bearer abc"
    assert request.headers["x-team"] == "blue"
    resource = json.loads(request.content)["resourceSpans"][0]["resource"]["attributes"]
    assert resource == [{"key": "service.name", "value": {"stringValue": "ops-laptop"}}]


@pytest.mark.parametrize(
    "env",
    [
        {"OTEL_EXPORTER_OTLP_ENDPOINT": ""},
        {"OTEL_SDK_DISABLED": "true"},
        {"OTEL_TRACES_EXPORTER": "none"},
        {"BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431"},
        {"BLIZZARD_TRACEPARENT": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"},
    ],
    ids=["no-endpoint", "sdk-disabled", "exporter-none", "worker-runner-url", "worker-traceparent"],
)
def test_nothing_is_sent_and_no_request_is_traced_when_the_gate_is_closed(env: dict[str, str]) -> None:
    collector, hub = _Collector(), _HubCalls()
    bound = _bind(collector, hub)

    result = _hub(bound, ["chunk", "list"], env)

    assert result.exit_code == 0, result.output
    assert collector.requests == []
    assert all("traceparent" not in h for h in hub.headers)


@pytest.mark.parametrize("argv", [["host", "--help"], ["record-marker", "--help"]])
def test_a_daemon_or_marker_command_is_never_traced(argv: list[str]) -> None:
    collector = _Collector()
    bound = _bind(collector)

    _hub(bound, argv)

    assert collector.requests == []


def test_a_command_run_through_the_hyphenated_alias_is_still_rooted_at_hub() -> None:
    collector = _Collector()
    bound = _bind(collector, _HubCalls())

    bound.runner.invoke(hub_group, ["chunk", "list"], env=_OPERATOR_ENV, prog_name="blizzard-hub")

    assert collector.spans()[0]["name"] == "hub chunk list"


def test_a_runner_operator_verb_is_a_span_and_its_requests_carry_the_context() -> None:
    collector = _Collector()
    seen: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "collector.local":
            return collector(request)
        seen.append(request)
        return httpx2.Response(200, json={})

    bound = bind_transport(answer)

    bound.runner.invoke(runner_group, ["status", "--runner-url", "http://127.0.0.1:1"], env=_OPERATOR_ENV)

    [span] = collector.spans()
    assert span["name"] == "runner status"
    assert seen, "the verb reached the runner"
    assert {r.headers["traceparent"] for r in seen} == {f"00-{span['traceId']}-{span['spanId']}-01"}


def test_the_unix_socket_client_carries_the_context(tmp_path: Path) -> None:
    @click.group(cls=operator_trace.OperatorGroup, lazy={}, trace_root="runner", invoke_without_command=True)
    def group() -> None: ...

    @group.command("probe")
    @click.pass_context
    def probe(ctx: click.Context) -> None:
        client = Door.of(ctx).uds_client(tmp_path / "s.sock")
        click.echo(client.headers.get("traceparent", ""))

    result = bind_transport(_Collector()).runner.invoke(group, ["probe"], env=_OPERATOR_ENV)

    assert parse_traceparent(result.output.strip()) is not None


def test_a_provider_call_of_external_usage_carries_no_trace_header() -> None:
    assert "OperatorTrace" not in inspect.getsource(external_usage)


def test_a_refused_send_changes_neither_output_nor_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    bound = _bind(_Collector(status=500), _HubCalls())

    traced = _hub(bound, ["chunk", "list"])
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    plain = bound.runner.invoke(hub_group, ["chunk", "list"], env={"BZ_HUB_URL": _OPERATOR_ENV["BZ_HUB_URL"]})

    assert (traced.exit_code, traced.output) == (plain.exit_code, plain.output)
    assert "trace send failed" not in traced.stderr


@pytest.mark.parametrize("hub_status", [200, 500])
def test_a_hung_endpoint_gives_up_at_the_cap_and_changes_neither_output_nor_exit_code(hub_status: int) -> None:
    bound = _bind(hub=_HubCalls(status=hub_status), reach_the_network=True)
    plain = bound.runner.invoke(hub_group, ["chunk", "list"], env={"BZ_HUB_URL": _OPERATOR_ENV["BZ_HUB_URL"]})
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    try:
        started = time.monotonic()
        hung = _hub(
            bound, ["chunk", "list"], {"OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{listener.getsockname()[1]}"}
        )
        elapsed = time.monotonic() - started
    finally:
        listener.close()

    assert (hung.exit_code, hung.output) == (plain.exit_code, plain.output)
    assert cli_spans.OPERATOR_SEND_CAP_SECONDS * 0.9 <= elapsed < cli_spans.OPERATOR_SEND_CAP_SECONDS + 0.5


def test_the_destination_is_the_signal_endpoint_verbatim_or_the_base_plus_the_traces_path() -> None:
    base = OtlpDestination.of({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://c:4318/"})
    signal = OtlpDestination.of(
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://c:4318", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://t/x"}
    )
    assert base is not None and base.url == "http://c:4318/v1/traces"
    assert signal is not None and signal.url == "http://t/x"
    assert OtlpDestination.of({}) is None


def test_the_signal_headers_override_the_general_ones() -> None:
    destination = OtlpDestination.of(
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://c",
            "OTEL_EXPORTER_OTLP_HEADERS": "a=1",
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "b=2",
        }
    )
    assert destination is not None and dict(destination.headers) == {"b": "2"}


def test_headers_parse_as_percent_decoded_key_value_pairs() -> None:
    assert parse_headers("k=v%2Cw, =x, junk, a = b=c ") == {"k": "v,w", "a": "b=c"}


def test_a_root_span_has_a_trace_of_its_own_and_hex_ids() -> None:
    span = cli_spans.CliSpan.root("hub chunk list", clock=Clock())
    span.finish(0)
    payload = span.payload()["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert payload["traceId"] == f"{span.trace_id:032x}" and len(payload["traceId"]) == 32
    assert len(payload["spanId"]) == 16
    assert "parentSpanId" not in payload
    assert span.traceparent == f"00-{payload['traceId']}-{payload['spanId']}-01"


def _clocked(wall: int = 5_000, ticks: tuple[int, ...] = (0, 0, 40)) -> Clock:
    monotonic = iter(ticks)
    return Clock(wall_ns=lambda: wall, monotonic_ns=lambda: next(monotonic))


def test_a_root_span_carries_its_service_name_times_and_no_chunk_or_lease_attribute() -> None:
    span = cli_spans.CliSpan.root("hub chunk list", service_name="ops-laptop", clock=_clocked())
    span.finish(0)

    body = span.payload()["resourceSpans"][0]
    payload = body["scopeSpans"][0]["spans"][0]

    assert body["resource"]["attributes"] == [{"key": "service.name", "value": {"stringValue": "ops-laptop"}}]
    assert (payload["startTimeUnixNano"], payload["endTimeUnixNano"]) == ("5000", "5040")
    assert [a["key"] for a in payload["attributes"]] == ["blizzard.cli.command", "process.exit.code"]
    assert "status" not in payload


def test_an_unfinished_span_ends_where_it_started() -> None:
    span = cli_spans.CliSpan.root("hub chunk list", clock=_clocked(ticks=(0, 0)))

    payload = span.payload()["resourceSpans"][0]["scopeSpans"][0]["spans"][0]

    assert payload["endTimeUnixNano"] == payload["startTimeUnixNano"] == "5000"


@pytest.mark.parametrize(
    ("endpoint", "url"),
    [
        ("http://c:4318//", "http://c:4318/v1/traces"),
        ("http://c:4318///", "http://c:4318/v1/traces"),
        ("http://c:4318/base/", "http://c:4318/base/v1/traces"),
    ],
)
def test_every_trailing_slash_of_the_general_endpoint_is_dropped_before_the_traces_path(
    endpoint: str, url: str
) -> None:
    destination = OtlpDestination.of({"OTEL_EXPORTER_OTLP_ENDPOINT": endpoint})

    assert destination is not None and destination.url == url


def test_the_signal_endpoint_keeps_its_trailing_slash() -> None:
    destination = OtlpDestination.of({"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://t/x/"})

    assert destination is not None and destination.url == "http://t/x/"


def test_a_signal_header_beats_the_same_general_header_and_a_blank_signal_setting_defers() -> None:
    both = OtlpDestination.of(
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://c",
            "OTEL_EXPORTER_OTLP_HEADERS": "a=general,b=general",
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "a=signal",
        }
    )
    blank = OtlpDestination.of(
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://c",
            "OTEL_EXPORTER_OTLP_HEADERS": "a=general",
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "  ",
        }
    )

    assert both is not None and dict(both.headers) == {"a": "signal"}
    assert blank is not None and dict(blank.headers) == {"a": "general"}


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (click.exceptions.Exit(3), 3),
        (click.exceptions.Exit(), 0),
        (click.ClickException("x"), 1),
        (click.UsageError("x"), 2),
        (click.Abort(), 1),
        (SystemExit(4), 4),
        (SystemExit(0), 0),
        (SystemExit(None), 0),
        (SystemExit("a message"), 1),
        (ValueError("x"), 1),
        (KeyboardInterrupt(), 1),
    ],
)
def test_the_exit_code_of_an_exception_is_the_one_click_would_end_the_process_with(
    exc: BaseException, code: int
) -> None:
    assert operator_trace.exit_code_of(exc) == code


def _grouped() -> click.Group:
    @click.group(cls=operator_trace.OperatorGroup, lazy={}, trace_root="hub")
    def group() -> None: ...

    @group.command("ok")
    def ok() -> None:
        click.echo("done")

    @group.command("boom")
    def boom() -> None:
        raise RuntimeError("planted failure")

    @group.command("quit")
    def quit_() -> None:
        raise SystemExit(3)

    return group


def _exit_attribute(span: dict) -> dict:
    return next(a for a in span["attributes"] if a["key"] == "process.exit.code")["value"]


@pytest.mark.parametrize(
    ("command", "code", "status"), [("ok", "0", None), ("boom", "1", {"code": 2}), ("quit", "3", {"code": 2})]
)
def test_the_operator_trace_records_the_exit_code_its_command_ends_in(
    command: str, code: str, status: dict | None
) -> None:
    collector = _Collector()
    bound = _bind(collector)

    bound.runner.invoke(_grouped(), [command], env=_OPERATOR_ENV)

    [span] = collector.spans()
    assert span["name"] == f"hub {command}"
    assert _exit_attribute(span) == {"intValue": code}
    assert span.get("status") == status


def test_the_operator_trace_returns_the_commands_value_and_finishes_once() -> None:
    trace = operator_trace.OperatorTrace("hub", CliCollaborators.production(), {})
    finished: list[int] = []
    trace.finish = finished.append  # type: ignore[method-assign]

    assert trace.run(lambda: "value") == "value"
    with pytest.raises(SystemExit):
        trace.run(lambda: (_ for _ in ()).throw(SystemExit(5)))

    assert finished == [0, 5]
