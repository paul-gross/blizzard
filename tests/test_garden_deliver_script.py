"""``garden_deliver``'s ``main()`` (blizzard#393 Phase 4, unit tier) — the
``tests/test_land_scripts.py`` shape: a scripted ``land_common.forge_request`` double,
env set through ``monkeypatch``, stdout/stderr asserted through ``capsys``."""

from __future__ import annotations

from typing import Any

import pytest

from blizzard.hub.graphs.scripts import garden_deliver, land_common

pytestmark = pytest.mark.unit

_DELIVERY_URL = "http://hub/garden-delivery"
_CALLBACK_URL = "http://hub/hub-markers"
_TOKEN = "marker-token"


def _set_env(monkeypatch: pytest.MonkeyPatch, *, with_callback_url: bool = True) -> None:
    monkeypatch.setenv("BZ_HUB_CHUNK_ID", "ch_1")
    monkeypatch.setenv("BZ_HUB_NODE_ID", "nd_1")
    monkeypatch.setenv("BZ_HUB_EPOCH", "1")
    monkeypatch.setenv("BZ_HUB_GARDEN_DELIVERY_URL", _DELIVERY_URL)
    monkeypatch.setenv("BZ_HUB_MARKER_TOKEN", _TOKEN)
    if with_callback_url:
        monkeypatch.setenv("BZ_HUB_MARKER_CALLBACK_URL", _CALLBACK_URL)
    else:
        monkeypatch.delenv("BZ_HUB_MARKER_CALLBACK_URL", raising=False)


def _scripted_forge(calls: list[tuple[str, str, dict[str, Any] | None, dict[str, str] | None]], *, delivery_response):
    """A double for ``land_common.forge_request``: any POST to ``_DELIVERY_URL`` returns
    ``delivery_response`` (a ``(status, body)`` pair); a POST to ``_CALLBACK_URL`` — the
    failure-marker write — always succeeds unless overridden by a call-site monkeypatch."""

    def fake(
        method: str,
        url: str,
        *,
        token: str | None,
        body: dict[str, Any] | None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        calls.append((method, url, body, headers))
        if url == _DELIVERY_URL:
            return delivery_response
        if url == _CALLBACK_URL:
            return 200, {}
        raise AssertionError(f"unexpected request to {url}")

    return fake


def test_a_missing_required_env_var_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("BZ_HUB_GARDEN_DELIVERY_URL", raising=False)
    monkeypatch.setenv("BZ_HUB_CHUNK_ID", "ch_1")
    monkeypatch.setenv("BZ_HUB_NODE_ID", "nd_1")
    monkeypatch.setenv("BZ_HUB_EPOCH", "1")
    monkeypatch.setattr("sys.argv", ["garden_deliver"])
    monkeypatch.setattr(
        land_common, "forge_request", lambda *a, **k: pytest.fail("must not contact the hub"), raising=False
    )

    with pytest.raises(SystemExit) as exc:
        garden_deliver.main()

    assert exc.value.code == 1
    assert "BZ_HUB_GARDEN_DELIVERY_URL" in capsys.readouterr().err


def test_a_recorded_outcome_prints_recorded_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_env(monkeypatch)
    calls: list[tuple[str, str, dict[str, Any] | None, dict[str, str] | None]] = []
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _scripted_forge(calls, delivery_response=(200, {"outcome": "recorded", "detail": ""})),
    )
    monkeypatch.setattr("sys.argv", ["garden_deliver", "--delta", "delta", "--proposals", "docket"])

    assert garden_deliver.main() == 0

    assert capsys.readouterr().out.strip().splitlines()[-1] == "recorded"
    delivery_calls = [c for c in calls if c[1] == _DELIVERY_URL]
    assert len(delivery_calls) == 1
    assert delivery_calls[0][2] == {"delta": ["delta"], "proposals": ["docket"]}
    assert delivery_calls[0][3] == {"X-Blizzard-Marker-Token": _TOKEN}


def test_an_invalid_outcome_writes_the_failure_marker_and_prints_invalid(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_env(monkeypatch)
    calls: list[tuple[str, str, dict[str, Any] | None, dict[str, str] | None]] = []
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _scripted_forge(calls, delivery_response=(200, {"outcome": "invalid", "detail": "bad delta"})),
    )
    monkeypatch.setattr("sys.argv", ["garden_deliver", "--delta", "delta"])

    assert garden_deliver.main() == 0

    assert capsys.readouterr().out.strip().splitlines()[-1] == "invalid"
    marker_calls = [c for c in calls if c[1] == _CALLBACK_URL]
    assert len(marker_calls) == 1
    assert marker_calls[0][2] == {"name": "garden-delivery-failure", "content": "bad delta"}
    assert marker_calls[0][3] == {"X-Blizzard-Marker-Token": _TOKEN}


def test_a_non_2xx_delivery_response_exits_non_zero_and_prints_neither_outcome(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_env(monkeypatch)
    calls: list[tuple[str, str, dict[str, Any] | None, dict[str, str] | None]] = []
    monkeypatch.setattr(
        land_common, "forge_request", _scripted_forge(calls, delivery_response=(503, {"message": "down"}))
    )
    monkeypatch.setattr("sys.argv", ["garden_deliver", "--delta", "delta"])

    exit_code = garden_deliver.main()

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "recorded" not in captured.out
    assert "invalid" not in captured.out


def test_a_marker_write_failure_on_the_invalid_path_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_env(monkeypatch)
    calls: list[tuple[str, str, dict[str, Any] | None, dict[str, str] | None]] = []

    def fake(
        method: str,
        url: str,
        *,
        token: str | None,
        body: dict[str, Any] | None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        calls.append((method, url, body, headers))
        if url == _DELIVERY_URL:
            return 200, {"outcome": "invalid", "detail": "bad delta"}
        return 401, {"message": "nope"}

    monkeypatch.setattr(land_common, "forge_request", fake)
    monkeypatch.setattr("sys.argv", ["garden_deliver", "--delta", "delta"])

    exit_code = garden_deliver.main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "marker write failed" in captured.err
    assert "recorded" not in captured.out
    assert "invalid" not in captured.out


def test_invalid_outcome_with_no_callback_url_raises_marker_write_error_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_env(monkeypatch, with_callback_url=False)
    calls: list[tuple[str, str, dict[str, Any] | None, dict[str, str] | None]] = []
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _scripted_forge(calls, delivery_response=(200, {"outcome": "invalid", "detail": "bad delta"})),
    )
    monkeypatch.setattr("sys.argv", ["garden_deliver", "--delta", "delta"])

    assert garden_deliver.main() == 1

    assert "BZ_HUB_MARKER_CALLBACK_URL" in capsys.readouterr().err
