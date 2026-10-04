"""A worker CLI command is one span: its context injected, its exit code recorded, its send capped —
and none of it changes the command's output or exit code. Unit tier (blizzard:unit-test)."""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Mapping

import httpx
import pytest
from click.testing import CliRunner
from google.protobuf.json_format import Parse
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from blizzard.foundation import cli_spans
from blizzard.foundation.trace_ids import format_traceparent, parse_traceparent
from blizzard.runner.cli import runner as runner_group
from tests.worker_http import bind_transport

pytestmark = pytest.mark.unit

_TRACE = "0af7651916cd43dd8448eb211c80319c"
_PARENT_SPAN = "b7ad6b7169203331"
_PARENT = f"00-{_TRACE}-{_PARENT_SPAN}-01"
_PLANTED = "planted text"
_ENV = {
    "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431",
    "BLIZZARD_LEASE_ID": "lease_9",
    "BLIZZARD_LEASE_TOKEN": "tok",
    "BLIZZARD_CHUNK_ID": "ch_1",
    "BLIZZARD_TRACEPARENT": _PARENT,
}


class _Recorder:
    def __init__(self, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/v1/traces":
            return httpx.Response(self.status)
        return httpx.Response(200, json=[])

    def traces(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/v1/traces"]

    def others(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path != "/v1/traces"]


def _run(argv: list[str], env: dict[str, str], input: str | None = None):
    return CliRunner().invoke(runner_group, argv, env=env, input=input)


def test_a_traced_command_injects_a_child_context_and_posts_one_span(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    bind_transport(monkeypatch, recorder)

    result = _run(["artifact", "list"], _ENV)

    assert result.exit_code == 0, result.output
    assert recorder.others(), "the command made requests"
    posts = recorder.traces()
    assert len(posts) == 1
    span = json.loads(posts[0].content)["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    for request in recorder.others():
        injected = parse_traceparent(request.headers["traceparent"])
        assert injected is not None
        assert f"{injected.trace_id:032x}" == _TRACE
        assert f"{injected.span_id:016x}" == span["spanId"]
    assert span["parentSpanId"] == _PARENT_SPAN
    assert span["name"] == "runner artifact list"
    assert posts[0].headers["X-Blizzard-Lease-Token"] == "tok"
    assert "traceparent" not in posts[0].headers


def test_the_payload_parses_as_an_otlp_export_request(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    bind_transport(monkeypatch, recorder)
    _run(["artifact", "list"], _ENV)

    body = json.loads(recorder.traces()[0].content)
    span = body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    for key in ("traceId", "spanId", "parentSpanId"):
        span[key] = base64.b64encode(bytes.fromhex(span[key])).decode()
    parsed = Parse(json.dumps(body), ExportTraceServiceRequest())

    scope_spans = parsed.resource_spans[0].scope_spans[0]
    assert (scope_spans.scope.name, scope_spans.scope.version) == ("blizzard.cli", "1")
    parsed_span = scope_spans.spans[0]
    assert parsed_span.trace_id == bytes.fromhex(_TRACE)
    assert parsed_span.end_time_unix_nano >= parsed_span.start_time_unix_nano > 10**18
    attributes = {a.key: a.value for a in parsed_span.attributes}
    assert attributes["blizzard.cli.command"].string_value == "runner artifact list"
    assert attributes["process.exit.code"].int_value == 0
    assert attributes["blizzard.lease.id"].string_value == "lease_9"
    assert attributes["blizzard.chunk.id"].string_value == "ch_1"


def test_the_command_name_carries_no_argument_value(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    bind_transport(monkeypatch, recorder)

    _run(["ask", _PLANTED], _ENV)

    posts = recorder.traces()
    assert len(posts) == 1
    assert _PLANTED not in posts[0].content.decode()
    assert json.loads(posts[0].content)["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["name"] == "runner ask"


def test_one_client_serves_every_request_and_the_span_post(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    clients = bind_transport(monkeypatch, recorder)

    _run(["artifact", "list"], _ENV)

    assert len(clients) == 1
    assert len(recorder.others()) >= 2
    assert clients[0].is_closed


def test_heartbeat_opens_no_span_and_injects_no_context(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    bind_transport(monkeypatch, recorder)

    result = _run(["heartbeat"], _ENV)

    assert result.exit_code == 0, result.output
    assert len(recorder.requests) == 1
    assert "traceparent" not in recorder.requests[0].headers
    assert not recorder.traces()


def test_session_end_is_traced(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    bind_transport(monkeypatch, recorder)

    _run(["session-end"], _ENV)

    assert len(recorder.traces()) == 1
    assert "traceparent" in recorder.others()[0].headers


@pytest.mark.parametrize(
    "env",
    [
        {"BLIZZARD_TRACEPARENT": ""},
        {"BLIZZARD_TRACEPARENT": "garbage"},
        {"BLIZZARD_TRACEPARENT": f"00-{_TRACE}-{_PARENT_SPAN}-00"},
        {"BLIZZARD_TRACEPARENT": f"00-{'0' * 32}-{_PARENT_SPAN}-01"},
    ],
)
def test_an_absent_malformed_or_unsampled_parent_runs_untraced(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    recorder = _Recorder()
    bind_transport(monkeypatch, recorder)

    result = _run(["artifact", "list"], {**_ENV, **env})

    assert result.exit_code == 0, result.output
    assert not recorder.traces()
    assert all("traceparent" not in r.headers for r in recorder.requests)


def test_the_unprefixed_traceparent_is_never_read(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    bind_transport(monkeypatch, recorder)
    env = {k: v for k, v in _ENV.items() if k != "BLIZZARD_TRACEPARENT"}

    _run(["artifact", "list"], {**env, "TRACEPARENT": _PARENT})

    assert not recorder.traces()
    assert all("traceparent" not in r.headers for r in recorder.requests)


def test_a_failing_command_records_its_exit_code_and_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/traces":
            posted.append(json.loads(request.content))
            return httpx.Response(200)
        return httpx.Response(500, json={"detail": "boom"})

    bind_transport(monkeypatch, handler)

    result = _run(["artifact", "list"], _ENV)

    assert result.exit_code == 1
    assert "boom" in result.output
    span = posted[0]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert span["status"] == {"code": 2}
    assert {"key": "process.exit.code", "value": {"intValue": "1"}} in span["attributes"]


def test_a_refused_send_changes_neither_output_nor_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    untraced = _Recorder()
    bind_transport(monkeypatch, untraced)
    plain = _run(["artifact", "list"], {k: v for k, v in _ENV.items() if k != "BLIZZARD_TRACEPARENT"})

    traced = _Recorder(status=404)
    bind_transport(monkeypatch, traced)
    result = _run(["artifact", "list"], _ENV)

    assert (result.exit_code, result.stdout) == (plain.exit_code, plain.stdout)
    assert "trace send failed" not in result.stderr


def test_a_refused_send_is_reported_only_under_the_debug_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    bind_transport(monkeypatch, _Recorder(status=404))

    result = _run(["artifact", "list"], {**_ENV, "BLIZZARD_TRACE_DEBUG": "1"})

    assert result.exit_code == 0
    assert "trace send failed" in result.stderr


def test_the_send_stops_at_the_total_cap_against_a_listener_that_never_answers() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    span = cli_spans.CliSpan.open(parse_traceparent(_PARENT), "runner ask")  # type: ignore[arg-type]
    span.finish(0)
    try:
        with httpx.Client() as client:
            started = time.monotonic()
            finished = cli_spans.send(
                client, f"http://127.0.0.1:{listener.getsockname()[1]}", span, headers={}, environ={}
            )
            elapsed = time.monotonic() - started
    finally:
        listener.close()

    assert finished is False
    assert elapsed < cli_spans.SEND_CAP_SECONDS + 0.1


def test_the_clock_is_one_wall_anchor_plus_monotonic_deltas() -> None:
    wall = iter([1_000, 9_999_999])
    monotonic = iter([50, 80])
    clock = cli_spans.Clock(wall_ns=lambda: next(wall), monotonic_ns=lambda: next(monotonic))

    assert clock.now_ns() == 1_030


def test_the_span_id_is_fresh_and_hex_encoded() -> None:
    parent = parse_traceparent(_PARENT)
    assert parent is not None
    first = cli_spans.CliSpan.open(parent, "runner ask")
    second = cli_spans.CliSpan.open(parent, "runner ask")
    assert first.span_id != second.span_id
    assert len(first.payload()["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["spanId"]) == 16


@pytest.mark.parametrize(
    "value",
    [
        "",
        "x",
        f"ff-{_TRACE}-{_PARENT_SPAN}-01",
        f"00-{_TRACE}-{'0' * 16}-01",
        f"00-{_TRACE}-{_PARENT_SPAN}-01-extra",
        f"00-{_TRACE.upper()}-{_PARENT_SPAN}-01",
        f"00-{_TRACE[:-1]}-{_PARENT_SPAN}-01",
    ],
)
def test_malformed_traceparents_do_not_parse(value: str) -> None:
    assert parse_traceparent(value) is None


def test_a_traceparent_round_trips() -> None:
    parsed = parse_traceparent(_PARENT)
    assert parsed is not None
    assert format_traceparent(parsed.trace_id, parsed.span_id, parsed.trace_flags) == _PARENT


def _loaded(env: dict[str, str]) -> set[str]:
    code = (
        "import sys, json; from click.testing import CliRunner; from blizzard.cli.main import blizzard; "
        "import blizzard.runner.cli.worker_call; print(json.dumps(sorted(sys.modules)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env={**os.environ, **env}
    )
    return set(json.loads(out.stdout))


def test_the_module_set_after_import_is_the_same_with_tracing_on_and_off() -> None:
    on = _loaded({"BLIZZARD_TRACEPARENT": _PARENT, "BLIZZARD_RUNNER_URL": "http://127.0.0.1:1"})
    off = _loaded({"BLIZZARD_TRACEPARENT": "", "BLIZZARD_RUNNER_URL": ""})
    assert on == off
    assert "blizzard.foundation.cli_spans" not in on


class _Post:
    """A Poster that records the call it is handed and answers a canned status."""

    def __init__(self, status: object = 200) -> None:
        self.status = status
        self.calls: list[dict] = []
        self.daemon: bool | None = None

    def post(self, url: str, *, content: bytes, headers: Mapping[str, str], timeout: float) -> object:
        import threading

        self.daemon = threading.current_thread().daemon
        self.calls.append({"url": url, "content": content, "headers": dict(headers), "timeout": timeout})
        return type("Response", (), {"status_code": self.status})()


def _open_span() -> cli_spans.CliSpan:
    span = cli_spans.CliSpan.open(parse_traceparent(_PARENT), "runner ask")  # type: ignore[arg-type]
    span.finish(0)
    return span


def test_the_send_posts_json_with_the_callers_headers_the_url_verbatim_and_the_cap_as_timeout() -> None:
    poster = _Post()

    finished = cli_spans.send(
        poster,
        "http://runner:1/v1/traces/",
        _open_span(),
        headers={"X-Blizzard-Lease-Token": "tok"},
        cap=0.7,
        environ={},
    )

    assert finished is True
    [call] = poster.calls
    assert call["url"] == "http://runner:1/v1/traces/"
    assert call["headers"] == {"Content-Type": "application/json", "X-Blizzard-Lease-Token": "tok"}
    assert call["timeout"] == 0.7
    assert json.loads(call["content"])["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["name"] == "runner ask"
    assert b", " not in call["content"] and b": " not in call["content"]


def test_the_send_runs_on_a_daemon_thread_so_a_hung_post_cannot_hold_the_process() -> None:
    poster = _Post()

    cli_spans.send(poster, "http://runner:1", _open_span(), headers={}, environ={})

    assert poster.daemon is True


@pytest.mark.parametrize(("status", "reported"), [(399, False), (400, True), (404, True), (500, True)])
def test_a_status_of_400_or_more_is_a_failure_reported_only_under_debug(
    capsys: pytest.CaptureFixture[str], status: int, reported: bool
) -> None:
    cli_spans.send(_Post(status), "http://runner:1", _open_span(), headers={}, environ={"BLIZZARD_TRACE_DEBUG": "1"})

    assert (f"trace send failed (the receiver answered {status})" in capsys.readouterr().err) is reported


def test_a_failure_is_silent_without_the_debug_flag_and_a_non_integer_status_is_no_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_spans.send(_Post(500), "http://runner:1", _open_span(), headers={}, environ={})
    cli_spans.send(_Post("500"), "http://runner:1", _open_span(), headers={}, environ={"BLIZZARD_TRACE_DEBUG": "1"})

    assert capsys.readouterr().err == ""


def test_a_post_that_raises_is_swallowed_and_named_under_debug(capsys: pytest.CaptureFixture[str]) -> None:
    class Failing:
        def post(self, url: str, **kwargs: object) -> object:
            raise ConnectionError("refused")

    finished = cli_spans.send(
        Failing(), "http://runner:1", _open_span(), headers={}, environ={"BLIZZARD_TRACE_DEBUG": "1"}
    )

    assert finished is True
    assert "blizzard: trace send failed (ConnectionError: refused)" in capsys.readouterr().err


def test_a_post_that_outlasts_the_cap_is_abandoned_and_reported(capsys: pytest.CaptureFixture[str]) -> None:
    import threading

    release = threading.Event()

    class Hung:
        def post(self, url: str, **kwargs: object) -> object:
            release.wait(5)
            return None

    try:
        started = time.monotonic()
        finished = cli_spans.send(
            Hung(), "http://runner:1", _open_span(), headers={}, cap=0.05, environ={"BLIZZARD_TRACE_DEBUG": "1"}
        )
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert finished is False
    assert 0.04 <= elapsed < 1
    assert "no answer within 50 ms" in capsys.readouterr().err


def test_an_open_span_carries_its_chunk_lease_and_start_time_and_parent() -> None:
    wall, ticks = iter([7_000]), iter([100, 100, 130])
    clock = cli_spans.Clock(wall_ns=lambda: next(wall), monotonic_ns=lambda: next(ticks))
    parent = parse_traceparent(_PARENT)
    assert parent is not None

    span = cli_spans.CliSpan.open(parent, "runner ask", chunk_id="ch_1", lease_id="lease_9", clock=clock)
    span.finish(2)

    payload = span.payload()["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    attributes = {a["key"]: a["value"] for a in payload["attributes"]}
    assert attributes["blizzard.chunk.id"] == {"stringValue": "ch_1"}
    assert attributes["blizzard.lease.id"] == {"stringValue": "lease_9"}
    assert attributes["process.exit.code"] == {"intValue": "2"}
    assert (payload["startTimeUnixNano"], payload["endTimeUnixNano"]) == ("7000", "7030")
    assert payload["parentSpanId"] == _PARENT_SPAN
    assert payload["traceId"] == _TRACE
    assert payload["status"] == {"code": 2}


def test_a_zero_random_id_is_replaced_by_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_spans.secrets, "randbits", lambda _bits: 0)

    root = cli_spans.CliSpan.root("hub chunk list")
    child = cli_spans.CliSpan.open(parse_traceparent(_PARENT), "runner ask")  # type: ignore[arg-type]

    assert (root.trace_id, root.span_id) == (1, 1)
    assert child.span_id == 1


def test_a_nonzero_random_id_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_spans.secrets, "randbits", lambda bits: (1 << bits) - 1)

    root = cli_spans.CliSpan.root("hub chunk list")

    assert (root.trace_id, root.span_id) == ((1 << 128) - 1, (1 << 64) - 1)
