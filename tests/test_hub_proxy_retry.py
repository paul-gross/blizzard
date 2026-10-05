"""``HubProxy.forward``'s bounded-backoff retry over a hub restart.

Unit-tier: ``HubProxy`` is built directly (no app, no store), its ``client`` a real
``httpx.Client`` over ``httpx.MockTransport`` so every response is scripted with no
socket, and its ``clock`` a ``ManualMonotonicClock`` whose sleeps advance its own reading —
so no test really waits, and the elapsed-time budget alone ends every retrying scenario."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.exceptions import HTTPException
from structlog.testing import capture_logs

from blizzard.foundation.clock import ManualMonotonicClock
from blizzard.runner.api.hub_proxy import _HUB_RETRY_CEILING, _RETRY_BACKOFF_SECONDS, HubProxy
from blizzard.runner.config import RunnerConfig

_HUB_URL = "http://hub.local:8421"


def _proxy(tmp_path: Path, client: httpx.Client, clock: ManualMonotonicClock) -> HubProxy:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL)
    return HubProxy(config, "test", client, clock)


@pytest.mark.unit
def test_a_transport_error_then_a_gateway_502_then_a_success_returns_the_success(tmp_path: Path) -> None:
    """The scripted restart window: a connection refused, then a `502` mid-swap, then the
    hub answers — every delay between them comes from the injected clock, not a real
    sleep, and the eventual response is the success, not any of the failures."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if len(calls) == 1:
            raise httpx.ConnectError("connection refused")
        if len(calls) == 2:
            return httpx.Response(502, json={"detail": "bad gateway"})
        return httpx.Response(200, json={"ok": True})

    clock = ManualMonotonicClock()
    proxy = _proxy(tmp_path, httpx.Client(transport=httpx.MockTransport(handler)), clock)

    resp = proxy.get("/api/fleet/chunks/ch_1")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert len(calls) == 3
    assert clock.delays == [_RETRY_BACKOFF_SECONDS[0], _RETRY_BACKOFF_SECONDS[1]]


@pytest.mark.unit
def test_a_persistent_transport_error_exhausts_and_raises_todays_502(tmp_path: Path) -> None:
    """Exhaustion on the transport-error arm raises exactly what a single failed attempt
    always raised — a ``502`` naming the hub unreachable, the transport error quoted."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        raise httpx.ConnectError("connection refused")

    clock = ManualMonotonicClock()
    proxy = _proxy(tmp_path, httpx.Client(transport=httpx.MockTransport(handler)), clock)

    with pytest.raises(HTTPException) as excinfo:
        proxy.get("/api/fleet/chunks/ch_1")

    assert excinfo.value.status_code == 502
    assert "connection refused" in excinfo.value.detail
    assert len(calls) > 1  # it did retry before giving up


@pytest.mark.unit
def test_a_persistent_gateway_status_exhausts_and_raises_the_upstream_status_and_detail(tmp_path: Path) -> None:
    """Exhaustion on the gateway-status arm raises the upstream's own status and detail,
    unwrapped exactly as a single failed attempt always unwrapped it."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(503, json={"detail": "hub restarting"})

    clock = ManualMonotonicClock()
    proxy = _proxy(tmp_path, httpx.Client(transport=httpx.MockTransport(handler)), clock)

    with pytest.raises(HTTPException) as excinfo:
        proxy.get("/api/fleet/chunks/ch_1")

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == "hub restarting"
    assert len(calls) > 1


@pytest.mark.unit
def test_a_post_is_never_retried_on_any_status(tmp_path: Path) -> None:
    """``pause``/``resume`` are the only non-``GET`` forwards — a gateway status on one
    of them raises on the first response, exactly as an unretried call always did."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(502, json={"detail": "bad gateway"})

    clock = ManualMonotonicClock()
    proxy = _proxy(tmp_path, httpx.Client(transport=httpx.MockTransport(handler)), clock)

    with pytest.raises(HTTPException) as excinfo:
        proxy.post("/api/fleet/chunks/ch_1/pause")

    assert excinfo.value.status_code == 502
    assert len(calls) == 1


@pytest.mark.unit
def test_a_non_gateway_status_on_a_get_raises_on_the_first_response(tmp_path: Path) -> None:
    """A `404`/`409`/`403` — anything outside the retryable gateway set — is never slowed
    by a retry it cannot benefit from."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(404, json={"detail": "unknown chunk"})

    clock = ManualMonotonicClock()
    proxy = _proxy(tmp_path, httpx.Client(transport=httpx.MockTransport(handler)), clock)

    with pytest.raises(HTTPException) as excinfo:
        proxy.get("/api/fleet/chunks/ch_1")

    assert excinfo.value.status_code == 404
    assert len(calls) == 1


@pytest.mark.unit
def test_a_recovered_forward_logs_once_below_error_distinct_from_a_failed_one(tmp_path: Path) -> None:
    """A forward that recovers logs exactly once, below ``ERROR``, under its own event name
    — an attempt that will be retried logs nothing."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if len(calls) == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json={"ok": True})

    clock = ManualMonotonicClock()
    proxy = _proxy(tmp_path, httpx.Client(transport=httpx.MockTransport(handler)), clock)

    with capture_logs() as logs:
        resp = proxy.get("/api/fleet/chunks/ch_1")

    assert resp.status_code == 200
    assert len(logs) == 1
    assert logs[0]["log_level"] != "error"
    assert logs[0]["event"] != "test proxy could not reach the hub"


@pytest.mark.unit
def test_an_exhausted_forwards_log_line_is_unchanged_in_level_and_fields(tmp_path: Path) -> None:
    """An exhausted forward's line is exactly today's — one entry, today's event and
    ``severity``, the same structured fields a single failed attempt always carried."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    clock = ManualMonotonicClock()
    proxy = _proxy(tmp_path, httpx.Client(transport=httpx.MockTransport(handler)), clock)

    with capture_logs() as logs, pytest.raises(HTTPException):
        proxy.get("/api/fleet/chunks/ch_1", severity="warning", subject="ch_1")

    unreachable = [entry for entry in logs if entry["event"] == "test proxy could not reach the hub"]
    assert len(unreachable) == 1
    assert unreachable[0]["log_level"] == "warning"
    assert unreachable[0]["subject"] == "ch_1"
    assert "connection refused" in unreachable[0]["error"]


@pytest.mark.unit
def test_a_retry_is_not_scheduled_when_its_backoff_would_overrun_the_budget(tmp_path: Path) -> None:
    """A retry is only scheduled when its *whole* backoff still lands inside the budget —
    gating on any positive remainder let a backoff committed on a sliver of budget carry
    real elapsed time past the ceiling before the next attempt ever fired, since the delay
    itself was never charged against the check that authorized it."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    clock = ManualMonotonicClock()
    proxy = _proxy(tmp_path, httpx.Client(transport=httpx.MockTransport(handler)), clock)

    # Budget 8.0s against the real backoff schedule (0.5, 1.0, 2.0, 4.0, 8.0): the fourth
    # backoff (4.0s) still fits (4.5s remaining), but the fifth (8.0s) does not (0.5s
    # remaining).
    with pytest.raises(HTTPException) as excinfo:
        proxy.get("/api/fleet/chunks/ch_1", timeout=8.0)

    assert excinfo.value.status_code == 502
    assert clock.delays == list(_RETRY_BACKOFF_SECONDS[:4])
    assert clock.reading <= 8.0


@pytest.mark.unit
def test_the_exhausted_schedule_under_the_default_ceiling_is_bounded_by_the_budget_alone(tmp_path: Path) -> None:
    """With instantaneous attempts, the default ceiling admits every backoff that fits —
    the last value repeating — and no count cap ends it early."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    clock = ManualMonotonicClock()
    proxy = _proxy(tmp_path, httpx.Client(transport=httpx.MockTransport(handler)), clock)

    with pytest.raises(HTTPException):
        proxy.get("/api/fleet/chunks/ch_1")

    assert clock.delays == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0]
    assert clock.reading <= _HUB_RETRY_CEILING


@pytest.mark.unit
def test_a_caller_supplied_timeout_permitting_fewer_retries_ends_the_schedule_sooner(tmp_path: Path) -> None:
    """A narrower caller budget cuts the schedule at the first backoff that no longer fits."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    clock = ManualMonotonicClock()
    proxy = _proxy(tmp_path, httpx.Client(transport=httpx.MockTransport(handler)), clock)

    with pytest.raises(HTTPException):
        proxy.get("/api/fleet/chunks/ch_1", timeout=2.0)

    assert clock.delays == [0.5, 1.0]


@pytest.mark.unit
def test_an_attempt_that_consumes_budget_cuts_the_schedule_short(tmp_path: Path) -> None:
    """Time an attempt itself spends counts against the budget: a transport that burns
    6s per attempt leaves room for one backoff, not the whole schedule."""
    clock = ManualMonotonicClock()

    def handler(request: httpx.Request) -> httpx.Response:
        clock.advance(6.0)
        raise httpx.ConnectError("connection refused")

    proxy = _proxy(tmp_path, httpx.Client(transport=httpx.MockTransport(handler)), clock)

    with pytest.raises(HTTPException):
        proxy.get("/api/fleet/chunks/ch_1", timeout=10.0)

    # 6s elapsed after attempt one: 4s left > 0.5 backoff. After the delay and attempt two,
    # 12.5s elapsed: nothing left.
    assert clock.delays == [0.5]


@pytest.mark.unit
def test_a_caller_supplied_timeout_caps_every_attempt_and_the_whole_forward(tmp_path: Path) -> None:
    """A caller-narrower-than-``_HUB_TIMEOUT`` budget (the dashboard's 3s) caps every
    attempt at that budget, not the module's 15s per-attempt bound, and the forward still
    exhausts and raises rather than retrying forever."""
    seen_timeouts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_timeouts.append(request.extensions["timeout"]["pool"])
        raise httpx.ConnectError("connection refused")

    clock = ManualMonotonicClock()
    proxy = _proxy(tmp_path, httpx.Client(transport=httpx.MockTransport(handler)), clock)

    with pytest.raises(HTTPException) as excinfo:
        proxy.get("/api/fleet/chunks/ch_1", timeout=3.0)

    assert excinfo.value.status_code == 502
    assert seen_timeouts[0] == 3.0
    assert all(t <= 3.0 for t in seen_timeouts)


def _refusing_proxy(tmp_path: Path, clock: ManualMonotonicClock, seen_timeouts: list[float]) -> HubProxy:
    def handler(request: httpx.Request) -> httpx.Response:
        seen_timeouts.append(request.extensions["timeout"]["pool"])
        raise httpx.ConnectError("connection refused")

    return _proxy(tmp_path, httpx.Client(transport=httpx.MockTransport(handler)), clock)


@pytest.mark.unit
def test_each_attempt_and_backoff_is_drawn_from_the_budget_elapsed_since_a_nonzero_start(tmp_path: Path) -> None:
    """Elapsed time is measured from the forward's own start, not from the clock's zero:
    over a clock already reading 100s, an 8s budget gives each attempt exactly what is left
    and schedules every backoff that still fits whole."""
    seen_timeouts: list[float] = []
    clock = ManualMonotonicClock(reading=100.0)
    proxy = _refusing_proxy(tmp_path, clock, seen_timeouts)

    with pytest.raises(HTTPException):
        proxy.get("/api/fleet/chunks/ch_1", timeout=8.0)

    assert seen_timeouts == [8.0, 7.5, 6.5, 4.5, 0.5]
    assert clock.delays == [0.5, 1.0, 2.0, 4.0]


@pytest.mark.unit
def test_a_remaining_budget_exactly_equal_to_the_next_backoff_stops_retrying(tmp_path: Path) -> None:
    """A backoff that would land exactly on the budget's end is not scheduled: after one
    0.5s delay a 1.5s budget has 1.0s left, which equals the next 1s backoff."""
    seen_timeouts: list[float] = []
    clock = ManualMonotonicClock(reading=100.0)
    proxy = _refusing_proxy(tmp_path, clock, seen_timeouts)

    with pytest.raises(HTTPException):
        proxy.get("/api/fleet/chunks/ch_1", timeout=1.5)

    assert clock.delays == [0.5]
    assert seen_timeouts == [1.5, 1.0]
