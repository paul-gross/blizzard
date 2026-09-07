"""``OpenAISubscriptionSampler.sample``.

Driven with an injected ``httpx.Client`` (an ``httpx.MockTransport``-backed fake) and an injected
``FixedClock`` — no real credential file location, no real network. Every failure path returns
``None`` and logs exactly one warning, never raises; the credential file is asserted read-only."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.runner.subscriptions.internal.openai_subscription_sampler import OpenAISubscriptionSampler

_NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)
_ACCOUNT_ID = "acct-0e1c5883"

_FIVE_HOURS = 18_000
_SEVEN_DAYS = 604_800


def _jwt(*, expires_at: datetime | None) -> str:
    """A minimally-shaped unsigned JWT — only the ``exp`` claim is ever read."""
    claims: dict[str, object] = {"sub": "user-1"}
    if expires_at is not None:
        claims["exp"] = expires_at.timestamp()
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def _credentials_text(
    *,
    access_token: str | None,
    account_id: str | None = _ACCOUNT_ID,
    tokens_block: bool = True,
) -> str:
    if not tokens_block:
        return json.dumps({"auth_mode": "chatgpt"})
    tokens: dict[str, object] = {}
    if access_token is not None:
        tokens["access_token"] = access_token
    if account_id is not None:
        tokens["account_id"] = account_id
    return json.dumps({"auth_mode": "chatgpt", "tokens": tokens})


def _write_credentials(path: Path, **kwargs: object) -> Path:
    path.write_text(_credentials_text(**kwargs))  # type: ignore[arg-type]
    return path


def _live_credentials(path: Path, clock: FixedClock) -> Path:
    return _write_credentials(path, access_token=_jwt(expires_at=clock.instant + timedelta(days=5)))


def _sampler(
    credentials_path: Path,
    handler,  # type: ignore[no-untyped-def]
    *,
    clock: FixedClock | None = None,
) -> OpenAISubscriptionSampler:
    transport = httpx.MockTransport(handler)
    return OpenAISubscriptionSampler(
        credentials_path=str(credentials_path),
        usage_api_base="https://chatgpt.test/backend-api",
        http_client=httpx.Client(transport=transport),
        clock=clock or FixedClock(_NOW),
    )


def _usage_body(
    *,
    primary: dict[str, object] | None = None,
    secondary: dict[str, object] | None = None,
) -> dict[str, object]:
    rate_limit: dict[str, object] = {}
    if primary is not None:
        rate_limit["primary_window"] = primary
    if secondary is not None:
        rate_limit["secondary_window"] = secondary
    return {"plan_type": "plus", "rate_limit": rate_limit}


def _window(*, used_percent: object, window_seconds: object, reset_at: object) -> dict[str, object]:
    return {"used_percent": used_percent, "limit_window_seconds": window_seconds, "reset_at": reset_at}


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json=_usage_body(
            primary=_window(used_percent=16.0, window_seconds=_FIVE_HOURS, reset_at=_NOW.timestamp() + _FIVE_HOURS),
            secondary=_window(used_percent=75.0, window_seconds=_SEVEN_DAYS, reset_at=_NOW.timestamp() + _SEVEN_DAYS),
        ),
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
    creds = _live_credentials(tmp_path / "auth.json", clock)
    _guard_against_writes(monkeypatch, creds)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/backend-api/codex/usage"
        assert request.headers["Authorization"].startswith("Bearer ")
        assert request.headers["chatgpt-account-id"] == _ACCOUNT_ID
        return _ok_handler(request)

    snapshot = _sampler(creds, handler, clock=clock).sample()

    assert snapshot is not None
    assert snapshot.sampled_at == _NOW
    by_window = {w.window: w for w in snapshot.windows}
    assert set(by_window) == {"5h", "7d"}

    five_hour = by_window["5h"]
    # `used_percent` is already 0-100: a wrong `*100` would land here as 1600.0, a wrong
    # /100 read as 0.16 — neither survives this assertion.
    assert five_hour.utilization_pct == 16.0
    assert five_hour.window_seconds == _FIVE_HOURS
    assert five_hour.resets_at == _NOW + timedelta(seconds=_FIVE_HOURS)
    assert five_hour.resets_at.tzinfo is not None

    seven_day = by_window["7d"]
    assert seven_day.utilization_pct == 75.0
    assert seven_day.window_seconds == _SEVEN_DAYS
    assert seven_day.resets_at == _NOW + timedelta(seconds=_SEVEN_DAYS)


@pytest.mark.unit
def test_the_request_does_not_carry_the_default_client_user_agent(tmp_path: Path) -> None:
    """Pins the header the edge in front of this endpoint 403s on."""
    clock = FixedClock(_NOW)
    creds = _live_credentials(tmp_path / "auth.json", clock)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["User-Agent"])
        return _ok_handler(request)

    assert _sampler(creds, handler, clock=clock).sample() is not None
    assert seen and not seen[0].startswith("python-httpx")


@pytest.mark.unit
def test_a_window_absent_from_the_response_is_an_absent_entry_not_a_fabricated_zero(tmp_path: Path) -> None:
    clock = FixedClock(_NOW)
    creds = _live_credentials(tmp_path / "auth.json", clock)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_usage_body(
                primary=_window(used_percent=16.0, window_seconds=_FIVE_HOURS, reset_at=_NOW.timestamp() + 60),
            ),
        )

    snapshot = _sampler(creds, handler, clock=clock).sample()

    assert snapshot is not None
    assert [w.window for w in snapshot.windows] == ["5h"]


@pytest.mark.unit
def test_the_window_label_is_derived_from_the_reported_length_not_the_response_key(tmp_path: Path) -> None:
    clock = FixedClock(_NOW)
    creds = _live_credentials(tmp_path / "auth.json", clock)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_usage_body(
                primary=_window(used_percent=1.0, window_seconds=3_600, reset_at=_NOW.timestamp() + 60),
                secondary=_window(used_percent=2.0, window_seconds=2_592_000, reset_at=_NOW.timestamp() + 60),
            ),
        )

    snapshot = _sampler(creds, handler, clock=clock).sample()

    assert snapshot is not None
    assert [w.window for w in snapshot.windows] == ["1h", "30d"]


@pytest.mark.unit
def test_a_window_missing_its_length_is_skipped_rather_than_labelled_from_nothing(tmp_path: Path) -> None:
    clock = FixedClock(_NOW)
    creds = _live_credentials(tmp_path / "auth.json", clock)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_usage_body(
                primary=_window(used_percent=16.0, window_seconds=None, reset_at=_NOW.timestamp() + 60),
                secondary=_window(used_percent=75.0, window_seconds=_SEVEN_DAYS, reset_at=_NOW.timestamp() + 60),
            ),
        )

    snapshot = _sampler(creds, handler, clock=clock).sample()

    assert snapshot is not None
    assert [w.window for w in snapshot.windows] == ["7d"]


@pytest.mark.unit
def test_an_undecodable_access_token_still_reaches_the_server_which_is_the_authority(tmp_path: Path) -> None:
    clock = FixedClock(_NOW)
    creds = _write_credentials(tmp_path / "auth.json", access_token="opaque-not-a-jwt")

    assert _sampler(creds, _ok_handler, clock=clock).sample() is not None


# Failure paths: every one returns None, logs exactly one warning, raises nothing.


def _assert_one_warning(logs: Sequence[Mapping[str, object]]) -> None:
    warnings = [log for log in logs if log.get("log_level") == "warning"]
    assert len(warnings) == 1, f"expected exactly one warning, got {warnings}"


@pytest.mark.unit
def test_missing_credentials_file_returns_none_and_warns_once(tmp_path: Path) -> None:
    sampler = _sampler(tmp_path / "absent.json", _unreachable_handler)

    with capture_logs() as logs:
        assert sampler.sample() is None
    _assert_one_warning(logs)


@pytest.mark.unit
def test_malformed_json_returns_none_and_warns_once(tmp_path: Path) -> None:
    creds = tmp_path / "auth.json"
    creds.write_text("{not json")
    sampler = _sampler(creds, _unreachable_handler)

    with capture_logs() as logs:
        assert sampler.sample() is None
    _assert_one_warning(logs)


@pytest.mark.unit
def test_missing_tokens_block_returns_none_and_warns_once(tmp_path: Path) -> None:
    creds = _write_credentials(tmp_path / "auth.json", access_token=None, tokens_block=False)
    sampler = _sampler(creds, _unreachable_handler)

    with capture_logs() as logs:
        assert sampler.sample() is None
    _assert_one_warning(logs)


@pytest.mark.unit
def test_missing_access_token_returns_none_and_warns_once(tmp_path: Path) -> None:
    creds = _write_credentials(tmp_path / "auth.json", access_token=None)
    sampler = _sampler(creds, _unreachable_handler)

    with capture_logs() as logs:
        assert sampler.sample() is None
    _assert_one_warning(logs)


@pytest.mark.unit
def test_missing_account_id_returns_none_and_warns_once(tmp_path: Path) -> None:
    clock = FixedClock(_NOW)
    creds = _write_credentials(
        tmp_path / "auth.json", access_token=_jwt(expires_at=clock.instant + timedelta(days=5)), account_id=None
    )
    sampler = _sampler(creds, _unreachable_handler, clock=clock)

    with capture_logs() as logs:
        assert sampler.sample() is None
    _assert_one_warning(logs)


@pytest.mark.unit
def test_expired_token_returns_none_warns_once_and_never_writes_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins the read-only decision: the vendor CLI owns the refresh flow and its lock, and
    rotates the refresh token, so writing here could both corrupt the file mid-refresh and
    invalidate the login it just renewed. An expired token is a miss, never a write."""
    clock = FixedClock(_NOW)
    creds = _write_credentials(
        tmp_path / "auth.json", access_token=_jwt(expires_at=clock.instant - timedelta(minutes=1))
    )
    _guard_against_writes(monkeypatch, creds)
    before = creds.read_text()
    sampler = _sampler(creds, _unreachable_handler, clock=clock)

    with capture_logs() as logs:
        assert sampler.sample() is None
    _assert_one_warning(logs)
    assert creds.read_text() == before


@pytest.mark.unit
def test_non_2xx_response_returns_none_and_warns_once(tmp_path: Path) -> None:
    clock = FixedClock(_NOW)
    creds = _live_credentials(tmp_path / "auth.json", clock)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Unauthorized"})

    with capture_logs() as logs:
        assert _sampler(creds, handler, clock=clock).sample() is None
    _assert_one_warning(logs)


@pytest.mark.unit
def test_timeout_returns_none_and_warns_once(tmp_path: Path) -> None:
    clock = FixedClock(_NOW)
    creds = _live_credentials(tmp_path / "auth.json", clock)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with capture_logs() as logs:
        assert _sampler(creds, handler, clock=clock).sample() is None
    _assert_one_warning(logs)


@pytest.mark.unit
def test_connection_error_returns_none_and_warns_once(tmp_path: Path) -> None:
    clock = FixedClock(_NOW)
    creds = _live_credentials(tmp_path / "auth.json", clock)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    with capture_logs() as logs:
        assert _sampler(creds, handler, clock=clock).sample() is None
    _assert_one_warning(logs)


@pytest.mark.unit
def test_unexpected_response_shape_returns_none_and_warns_once(tmp_path: Path) -> None:
    clock = FixedClock(_NOW)
    creds = _live_credentials(tmp_path / "auth.json", clock)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "a", "mapping"])

    with capture_logs() as logs:
        assert _sampler(creds, handler, clock=clock).sample() is None
    _assert_one_warning(logs)


@pytest.mark.unit
def test_unparseable_response_body_returns_none_and_warns_once(tmp_path: Path) -> None:
    clock = FixedClock(_NOW)
    creds = _live_credentials(tmp_path / "auth.json", clock)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>", headers={"Content-Type": "application/json"})

    with capture_logs() as logs:
        assert _sampler(creds, handler, clock=clock).sample() is None
    _assert_one_warning(logs)


@pytest.mark.unit
def test_zero_parseable_windows_returns_none_and_warns_once(tmp_path: Path) -> None:
    clock = FixedClock(_NOW)
    creds = _live_credentials(tmp_path / "auth.json", clock)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_usage_body())

    with capture_logs() as logs:
        assert _sampler(creds, handler, clock=clock).sample() is None
    _assert_one_warning(logs)


@pytest.mark.unit
def test_an_absent_rate_limit_block_returns_none_and_warns_once(tmp_path: Path) -> None:
    clock = FixedClock(_NOW)
    creds = _live_credentials(tmp_path / "auth.json", clock)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"plan_type": "plus", "rate_limit": None})

    with capture_logs() as logs:
        assert _sampler(creds, handler, clock=clock).sample() is None
    _assert_one_warning(logs)


@pytest.mark.unit
def test_a_window_length_arriving_as_a_float_still_yields_an_integer_window(tmp_path: Path) -> None:
    clock = FixedClock(_NOW)
    creds = _live_credentials(tmp_path / "auth.json", clock)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_usage_body(
                primary=_window(used_percent=5.0, window_seconds=18_000.0, reset_at=_NOW.timestamp() + 60),
            ),
        )

    snapshot = _sampler(creds, handler, clock=clock).sample()

    assert snapshot is not None
    assert snapshot.windows[0].window == "5h"
    assert snapshot.windows[0].window_seconds == _FIVE_HOURS
    assert isinstance(snapshot.windows[0].window_seconds, int)


@pytest.mark.unit
@pytest.mark.parametrize("length", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_window_length_skips_its_window_instead_of_raising(tmp_path: Path, length: str) -> None:
    """JSON admits these literals, and converting one to an int raises."""
    clock = FixedClock(_NOW)
    creds = _live_credentials(tmp_path / "auth.json", clock)

    def handler(request: httpx.Request) -> httpx.Response:
        # Built as raw text: `json=` would refuse to serialize a non-finite float.
        return httpx.Response(
            200,
            content=(
                '{"rate_limit": {'
                f'"primary_window": {{"used_percent": 1.0, "limit_window_seconds": {length}, "reset_at": 1788825756}},'
                '"secondary_window": {"used_percent": 75.0, "limit_window_seconds": 604800, "reset_at": 1789412556}}}'
            ).encode(),
            headers={"Content-Type": "application/json"},
        )

    snapshot = _sampler(creds, handler, clock=clock).sample()

    assert snapshot is not None
    assert [w.window for w in snapshot.windows] == ["7d"]


@pytest.mark.unit
def test_two_windows_reporting_one_length_yield_a_single_labelled_window(tmp_path: Path) -> None:
    """The board keys its per-window render on the label, so a collision must not ship."""
    clock = FixedClock(_NOW)
    creds = _live_credentials(tmp_path / "auth.json", clock)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_usage_body(
                primary=_window(used_percent=10.0, window_seconds=_FIVE_HOURS, reset_at=_NOW.timestamp() + 60),
                secondary=_window(used_percent=20.0, window_seconds=_FIVE_HOURS, reset_at=_NOW.timestamp() + 90),
            ),
        )

    snapshot = _sampler(creds, handler, clock=clock).sample()

    assert snapshot is not None
    assert [w.window for w in snapshot.windows] == ["5h"]
    assert snapshot.windows[0].utilization_pct == 10.0


@pytest.mark.unit
def test_reset_at_epoch_seconds_and_iso_string_parse_to_the_same_instant(tmp_path: Path) -> None:
    clock = FixedClock(_NOW)
    creds = _live_credentials(tmp_path / "auth.json", clock)
    expected = _NOW + timedelta(seconds=_FIVE_HOURS)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_usage_body(
                primary=_window(used_percent=1.0, window_seconds=_FIVE_HOURS, reset_at=expected.timestamp()),
                secondary=_window(
                    used_percent=2.0,
                    window_seconds=_SEVEN_DAYS,
                    reset_at=expected.isoformat().replace("+00:00", "Z"),
                ),
            ),
        )

    snapshot = _sampler(creds, handler, clock=clock).sample()

    assert snapshot is not None
    by_window = {w.window: w for w in snapshot.windows}
    assert by_window["5h"].resets_at == by_window["7d"].resets_at == expected
