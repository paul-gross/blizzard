"""``land_common``'s shared helpers — unit tier (issue #230).

Exercises ``require_env``, ``require_json_env``, and ``marker_recorder`` directly, with
no forge or script involved, proving the shared durable-write and env-diagnostic
behavior without re-deriving it through a whole ``main()`` per script."""

from __future__ import annotations

from typing import Any

import pytest

from blizzard.hub.graphs.scripts import land_common

pytestmark = pytest.mark.unit

_REPO = "acme/widget"
_CALLBACK_URL = "http://callback/hub-markers"
_TOKEN = "test-marker-token"


# -- require_env / require_json_env -------------------------------------------------


def test_require_env_returns_the_set_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_VAR", "a-value")
    assert land_common.require_env("SOME_VAR") == "a-value"


def test_require_env_exits_non_zero_naming_the_missing_var(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SOME_VAR", raising=False)

    with pytest.raises(SystemExit) as exc:
        land_common.require_env("SOME_VAR")

    assert exc.value.code == 1
    assert "SOME_VAR" in capsys.readouterr().err


def test_require_json_env_parses_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_JSON", '{"a": 1}')
    assert land_common.require_json_env("SOME_JSON") == {"a": 1}


def test_require_json_env_exits_non_zero_naming_malformed_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SOME_JSON", "{not valid json")

    with pytest.raises(SystemExit) as exc:
        land_common.require_json_env("SOME_JSON")

    assert exc.value.code == 1
    assert "SOME_JSON" in capsys.readouterr().err


# -- marker_recorder ------------------------------------------------------------------


def _request_returning(*results: tuple[int, Any] | Exception):
    """A fake ``request(...)`` that returns (or raises) one scripted result per call, in
    order. Records every call's kwargs for assertion."""
    calls: list[dict[str, Any]] = []
    remaining = list(results)

    def fake(method: str, url: str, *, token: str | None, headers: dict[str, str] | None, body: Any) -> Any:
        calls.append({"method": method, "url": url, "token": token, "headers": headers, "body": body})
        result = remaining.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


def test_a_2xx_response_succeeds_with_no_retry() -> None:
    request = _request_returning((200, {"recorded": True}))
    record = land_common.marker_recorder(callback_url=_CALLBACK_URL, token=_TOKEN, request=request)

    record(_REPO, "sha1")

    assert len(request.calls) == 1  # type: ignore[attr-defined]


def test_the_idempotent_recorded_false_replay_still_succeeds() -> None:
    """A 2xx is success regardless of body — the hub's own idempotent replay of an
    already-recorded marker reports ``recorded: false`` and that is still durable."""
    request = _request_returning((200, {"recorded": False}))
    record = land_common.marker_recorder(callback_url=_CALLBACK_URL, token=_TOKEN, request=request)

    record(_REPO, "sha1")  # must not raise


def test_the_marker_post_carries_the_token_header() -> None:
    request = _request_returning((200, {}))
    record = land_common.marker_recorder(callback_url=_CALLBACK_URL, token=_TOKEN, request=request)

    record(_REPO, "sha1")

    call = request.calls[0]  # type: ignore[attr-defined]
    assert call["headers"] == {"X-Blizzard-Marker-Token": _TOKEN}
    assert call["token"] is None  # the marker write is never the forge's own token
    assert call["body"] == {"name": f"merged/{_REPO}", "content": "sha1"}


def test_an_empty_token_sends_no_marker_token_header() -> None:
    request = _request_returning((200, {}))
    record = land_common.marker_recorder(callback_url=_CALLBACK_URL, token="", request=request)

    record(_REPO, "sha1")

    assert request.calls[0]["headers"] is None  # type: ignore[attr-defined]


def test_a_5xx_then_a_2xx_retries_exactly_once_and_succeeds() -> None:
    request = _request_returning((503, {"message": "unavailable"}), (200, {}))
    record = land_common.marker_recorder(callback_url=_CALLBACK_URL, token=_TOKEN, request=request)

    record(_REPO, "sha1")  # must not raise

    assert len(request.calls) == 2  # type: ignore[attr-defined]


def test_a_5xx_on_every_attempt_raises_after_exactly_three_calls() -> None:
    request = _request_returning(
        (503, {"message": "unavailable"}), (503, {"message": "unavailable"}), (503, {"message": "unavailable"})
    )
    record = land_common.marker_recorder(callback_url=_CALLBACK_URL, token=_TOKEN, request=request)

    with pytest.raises(land_common.MarkerWriteError) as exc:
        record(_REPO, "sha1")

    assert len(request.calls) == 3  # type: ignore[attr-defined]
    assert _REPO in str(exc.value)
    assert "503" in str(exc.value)


def test_a_4xx_raises_immediately_with_no_retry() -> None:
    request = _request_returning((401, {"message": "unauthorized"}))
    record = land_common.marker_recorder(callback_url=_CALLBACK_URL, token=_TOKEN, request=request)

    with pytest.raises(land_common.MarkerWriteError) as exc:
        record(_REPO, "sha1")

    assert len(request.calls) == 1  # type: ignore[attr-defined]
    assert _REPO in str(exc.value)
    assert "401" in str(exc.value)


def test_a_connection_error_then_success_retries_and_succeeds() -> None:
    request = _request_returning(OSError("connection refused"), (200, {}))
    record = land_common.marker_recorder(callback_url=_CALLBACK_URL, token=_TOKEN, request=request)

    record(_REPO, "sha1")  # must not raise

    assert len(request.calls) == 2  # type: ignore[attr-defined]


def test_a_connection_error_on_every_attempt_raises_after_three_calls() -> None:
    request = _request_returning(
        OSError("connection refused"), OSError("connection refused"), OSError("connection refused")
    )
    record = land_common.marker_recorder(callback_url=_CALLBACK_URL, token=_TOKEN, request=request)

    with pytest.raises(land_common.MarkerWriteError):
        record(_REPO, "sha1")

    assert len(request.calls) == 3  # type: ignore[attr-defined]


def test_constructing_with_no_callback_url_is_not_fatal_by_itself() -> None:
    """A chunk with nothing pending never calls ``record`` — that must stay a silent
    no-op; only invoking the closure is fatal."""
    request = _request_returning()
    land_common.marker_recorder(callback_url="", token=_TOKEN, request=request)  # must not raise

    assert request.calls == []  # type: ignore[attr-defined]


def test_invoking_the_closure_with_no_callback_url_raises() -> None:
    request = _request_returning()
    record = land_common.marker_recorder(callback_url="", token=_TOKEN, request=request)

    with pytest.raises(land_common.MarkerWriteError) as exc:
        record(_REPO, "sha1")

    assert _REPO in str(exc.value)
    assert request.calls == []  # type: ignore[attr-defined]
