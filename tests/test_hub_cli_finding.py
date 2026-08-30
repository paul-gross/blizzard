"""``blizzard hub finding list|show`` (unit tier) — pure clients of the finding routes,
driven here with ``httpx`` stubbed (blizzard#390), the ``tests/test_hub_cli_scope.py``
shape."""

from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

from blizzard.hub.cli import hub as hub_group


class _FakeResponse:
    def __init__(self, status_code: int, payload: object | None = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)  # type: ignore[arg-type]


@pytest.mark.unit
def test_finding_list_passes_routine_scope_and_include_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_get(url: str, *, params: dict[str, str], timeout: float) -> _FakeResponse:
        calls.append((url, params))
        return _FakeResponse(200, [])

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(
        hub_group,
        ["finding", "list", "--routine", "nightly", "--scope", "blizzard", "--include-gone"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        ("http://hub.local:8421/api/findings", {"routine": "nightly", "scope": "blizzard", "include_gone": "true"})
    ]


@pytest.mark.unit
def test_finding_list_prints_each_row(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, params: dict[str, str], timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            [
                {
                    "finding_id": "fin_1",
                    "routine_name": "nightly",
                    "scope_slug": "blizzard",
                    "class": "stale-docstring",
                    "locus": "a.py:1",
                    "summary": "s",
                    "introduced": None,
                    "live": True,
                    "last_seen_at": "t0",
                    "observed_count": 0,
                }
            ],
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["finding", "list", "--routine", "nightly", "--scope", "blizzard"])

    assert result.exit_code == 0, result.output
    assert "fin_1" in result.output
    assert "live" in result.output


@pytest.mark.unit
def test_finding_show_renders_the_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            {
                "finding_id": "fin_1",
                "routine_name": "nightly",
                "scope_slug": "blizzard",
                "class": "stale-docstring",
                "locus": "a.py:1",
                "summary": "s",
                "introduced": "a1b2c3d",
                "live": True,
                "last_seen_at": "t0",
                "observed_count": 2,
            },
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["finding", "show", "fin_1"])

    assert result.exit_code == 0, result.output
    assert "fin_1" in result.output
    assert "a1b2c3d" in result.output


@pytest.mark.unit
def test_finding_show_unknown_id_reports_404(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {"detail": "unknown finding fin_ghost"})

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["finding", "show", "fin_ghost"])

    assert result.exit_code != 0
    assert "unknown finding fin_ghost" in result.output
