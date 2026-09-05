"""``ClaudeCodeAdapter.sample_external_subscription_usage`` (issue #218, phase 1).

Driven with an injected ``httpx.Client`` (an ``httpx.MockTransport``-backed fake) and
an injected ``FixedClock`` — no real credential file location, no real network. Every
failure path returns ``None`` and logs exactly one warning, never raises; the
credential file is asserted read-only throughout."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter

_NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


def _credentials_text(*, access_token: str | None = "tok-live", expires_at_ms: float | None) -> str:
    oauth: dict[str, object] = {}
    if access_token is not None:
        oauth["accessToken"] = access_token
    if expires_at_ms is not None:
        oauth["expiresAt"] = expires_at_ms
    return json.dumps({"claudeAiOauth": oauth})


def _write_credentials(path: Path, *, access_token: str | None = "tok-live", expires_at_ms: float | None) -> Path:
    path.write_text(_credentials_text(access_token=access_token, expires_at_ms=expires_at_ms))
    return path


def _future_expiry_ms(clock: FixedClock, hours: float = 1) -> float:
    from datetime import timedelta

    return (clock.instant + timedelta(hours=hours)).timestamp() * 1000


def _past_expiry_ms(clock: FixedClock, hours: float = 1) -> float:
    from datetime import timedelta

    return (clock.instant - timedelta(hours=hours)).timestamp() * 1000


def _adapter(
    credentials_path: Path,
    handler,  # type: ignore[no-untyped-def]
    *,
    clock: FixedClock | None = None,
) -> ClaudeCodeAdapter:
    transport = httpx.MockTransport(handler)
    return ClaudeCodeAdapter(
        credentials_path=str(credentials_path),
        usage_api_base="https://api.anthropic.test",
        http_client=httpx.Client(transport=transport),
        clock=clock or FixedClock(_NOW),
    )


def _unreachable_handler(request: httpx.Request) -> httpx.Response:
    raise AssertionError("the usage endpoint should not have been called")


def _guard_against_writes(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Fail the test if anything opens ``path`` in a write-capable mode."""
    original_open = Path.open

    def guarded_open(self: Path, mode: str = "r", *args: object, **kwargs: object) -> object:
        if self == path and any(flag in mode for flag in "wax+"):
            raise AssertionError(f"credentials file opened in a write-capable mode: {mode!r}")
        return original_open(self, mode, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", guarded_open)


# Happy path.


@pytest.mark.unit
def test_happy_path_parses_both_windows_with_correct_scale_and_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FixedClock(_NOW)
    creds = _write_credentials(tmp_path / ".credentials.json", expires_at_ms=_future_expiry_ms(clock))
    _guard_against_writes(monkeypatch, creds)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/oauth/usage"
        assert request.headers["Authorization"] == "Bearer tok-live"
        assert request.headers["anthropic-beta"] == "oauth-2025-04-20"
        assert request.headers["Content-Type"] == "application/json"
        return httpx.Response(
            200,
            json={
                "five_hour": {"utilization": 42.0, "resets_at": "2026-08-01T18:00:00Z"},
                "seven_day": {"utilization": 8.25, "resets_at": 1785700800},
            },
        )

    adapter = _adapter(creds, handler, clock=clock)
    before_mtime = creds.stat().st_mtime_ns

    snapshot = adapter.sample_external_subscription_usage()

    assert snapshot is not None
    assert snapshot.sampled_at == _NOW
    by_window = {w.window: w for w in snapshot.windows}
    assert set(by_window) == {"5h", "7d"}

    five_hour = by_window["5h"]
    # A wrong `*100` misreading would land here as 4200.0; a wrong /100 read would
    # land as 0.42 — neither survives this assertion.
    assert five_hour.utilization_pct == 42.0
    assert five_hour.window_seconds == 18_000
    assert five_hour.resets_at == datetime(2026, 8, 1, 18, 0, 0, tzinfo=UTC)
    assert five_hour.resets_at.tzinfo is not None

    seven_day = by_window["7d"]
    assert seven_day.utilization_pct == 8.25
    assert seven_day.window_seconds == 604_800
    assert seven_day.resets_at.tzinfo is not None

    assert creds.stat().st_mtime_ns == before_mtime


@pytest.mark.unit
def test_a_window_absent_from_the_response_is_an_absent_entry_not_a_fabricated_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FixedClock(_NOW)
    creds = _write_credentials(tmp_path / ".credentials.json", expires_at_ms=_future_expiry_ms(clock))
    _guard_against_writes(monkeypatch, creds)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"five_hour": {"utilization": 10.0, "resets_at": "2026-08-01T18:00:00Z"}, "seven_day": None}
        )

    snapshot = _adapter(creds, handler, clock=clock).sample_external_subscription_usage()

    assert snapshot is not None
    assert [w.window for w in snapshot.windows] == ["5h"]


# resets_at: epoch seconds and ISO-8601 coerce to the same instant.


@pytest.mark.unit
def test_resets_at_epoch_seconds_and_iso_string_parse_to_the_same_instant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FixedClock(_NOW)
    creds = _write_credentials(tmp_path / ".credentials.json", expires_at_ms=_future_expiry_ms(clock))
    _guard_against_writes(monkeypatch, creds)
    instant = datetime(2026, 8, 1, 18, 0, 0, tzinfo=UTC)

    def handler_epoch(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"five_hour": {"utilization": 1.0, "resets_at": instant.timestamp()}})

    def handler_iso(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"five_hour": {"utilization": 1.0, "resets_at": instant.isoformat()}})

    snap_epoch = _adapter(creds, handler_epoch, clock=clock).sample_external_subscription_usage()
    snap_iso = _adapter(creds, handler_iso, clock=clock).sample_external_subscription_usage()

    assert snap_epoch is not None and snap_iso is not None
    assert snap_epoch.windows[0].resets_at == snap_iso.windows[0].resets_at == instant


# Failure paths: every one returns None, logs exactly one warning, raises nothing.


@pytest.mark.unit
def test_missing_credentials_file_returns_none_and_warns_once(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path / "does-not-exist.json", _unreachable_handler)

    with capture_logs() as logs:
        result = adapter.sample_external_subscription_usage()

    assert result is None
    assert len([entry for entry in logs if entry["log_level"] == "warning"]) == 1


@pytest.mark.unit
def test_malformed_json_returns_none_and_warns_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    creds = tmp_path / ".credentials.json"
    creds.write_text("{not json")
    _guard_against_writes(monkeypatch, creds)
    adapter = _adapter(creds, _unreachable_handler)

    with capture_logs() as logs:
        result = adapter.sample_external_subscription_usage()

    assert result is None
    assert len([entry for entry in logs if entry["log_level"] == "warning"]) == 1


@pytest.mark.unit
def test_missing_access_token_returns_none_and_warns_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FixedClock(_NOW)
    creds = _write_credentials(
        tmp_path / ".credentials.json", access_token=None, expires_at_ms=_future_expiry_ms(clock)
    )
    _guard_against_writes(monkeypatch, creds)
    adapter = _adapter(creds, _unreachable_handler, clock=clock)

    with capture_logs() as logs:
        result = adapter.sample_external_subscription_usage()

    assert result is None
    assert len([entry for entry in logs if entry["log_level"] == "warning"]) == 1


@pytest.mark.unit
def test_expired_token_returns_none_warns_once_and_never_writes_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FixedClock(_NOW)
    creds = _write_credentials(tmp_path / ".credentials.json", expires_at_ms=_past_expiry_ms(clock))
    _guard_against_writes(monkeypatch, creds)
    before_mtime = creds.stat().st_mtime_ns
    adapter = _adapter(creds, _unreachable_handler, clock=clock)

    with capture_logs() as logs:
        result = adapter.sample_external_subscription_usage()

    assert result is None
    assert len([entry for entry in logs if entry["log_level"] == "warning"]) == 1
    assert creds.stat().st_mtime_ns == before_mtime


@pytest.mark.unit
def test_non_2xx_response_returns_none_and_warns_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FixedClock(_NOW)
    creds = _write_credentials(tmp_path / ".credentials.json", expires_at_ms=_future_expiry_ms(clock))
    _guard_against_writes(monkeypatch, creds)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid token"})

    adapter = _adapter(creds, handler, clock=clock)

    with capture_logs() as logs:
        result = adapter.sample_external_subscription_usage()

    assert result is None
    assert len([entry for entry in logs if entry["log_level"] == "warning"]) == 1


@pytest.mark.unit
def test_timeout_returns_none_and_warns_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FixedClock(_NOW)
    creds = _write_credentials(tmp_path / ".credentials.json", expires_at_ms=_future_expiry_ms(clock))
    _guard_against_writes(monkeypatch, creds)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    adapter = _adapter(creds, handler, clock=clock)

    with capture_logs() as logs:
        result = adapter.sample_external_subscription_usage()

    assert result is None
    assert len([entry for entry in logs if entry["log_level"] == "warning"]) == 1


@pytest.mark.unit
def test_connection_error_returns_none_and_warns_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FixedClock(_NOW)
    creds = _write_credentials(tmp_path / ".credentials.json", expires_at_ms=_future_expiry_ms(clock))
    _guard_against_writes(monkeypatch, creds)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    adapter = _adapter(creds, handler, clock=clock)

    with capture_logs() as logs:
        result = adapter.sample_external_subscription_usage()

    assert result is None
    assert len([entry for entry in logs if entry["log_level"] == "warning"]) == 1


@pytest.mark.unit
def test_unexpected_response_shape_returns_none_and_warns_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FixedClock(_NOW)
    creds = _write_credentials(tmp_path / ".credentials.json", expires_at_ms=_future_expiry_ms(clock))
    _guard_against_writes(monkeypatch, creds)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "an", "object"])

    adapter = _adapter(creds, handler, clock=clock)

    with capture_logs() as logs:
        result = adapter.sample_external_subscription_usage()

    assert result is None
    assert len([entry for entry in logs if entry["log_level"] == "warning"]) == 1


@pytest.mark.unit
def test_unparseable_response_body_returns_none_and_warns_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FixedClock(_NOW)
    creds = _write_credentials(tmp_path / ".credentials.json", expires_at_ms=_future_expiry_ms(clock))
    _guard_against_writes(monkeypatch, creds)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all")

    adapter = _adapter(creds, handler, clock=clock)

    with capture_logs() as logs:
        result = adapter.sample_external_subscription_usage()

    assert result is None
    assert len([entry for entry in logs if entry["log_level"] == "warning"]) == 1


@pytest.mark.unit
def test_zero_parseable_windows_returns_none_and_warns_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FixedClock(_NOW)
    creds = _write_credentials(tmp_path / ".credentials.json", expires_at_ms=_future_expiry_ms(clock))
    _guard_against_writes(monkeypatch, creds)

    def handler(request: httpx.Request) -> httpx.Response:
        # Both windows null, and a null utilization inside an otherwise-present entry —
        # neither carries usable data, so the parse yields zero windows.
        return httpx.Response(
            200, json={"five_hour": None, "seven_day": {"utilization": None, "resets_at": "2026-08-01T18:00:00Z"}}
        )

    adapter = _adapter(creds, handler, clock=clock)

    with capture_logs() as logs:
        result = adapter.sample_external_subscription_usage()

    assert result is None
    assert len([entry for entry in logs if entry["log_level"] == "warning"]) == 1
