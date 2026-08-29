"""``blizzard hub proposal list|show`` (unit tier) — pure clients of the garden-proposal
routes, driven here with ``httpx`` stubbed (blizzard#390), the
``tests/test_hub_cli_scope.py`` shape."""

from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

import blizzard.hub.cli as hub_cli
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
def test_proposal_list_prints_each_row(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            [
                {
                    "proposal_id": "prop_1",
                    "routine_name": "nightly",
                    "class": "fix-the-source",
                    "title": "Author a docstring standard",
                    "body": "the case",
                    "findings": ["fin_1"],
                    "created_at": "t0",
                }
            ],
        )

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["proposal", "list"])

    assert result.exit_code == 0, result.output
    assert "prop_1" in result.output
    assert "fix-the-source" in result.output


@pytest.mark.unit
def test_proposal_show_renders_the_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            {
                "proposal_id": "prop_1",
                "routine_name": "nightly",
                "class": "fix-the-source",
                "title": "Author a docstring standard",
                "body": "the case",
                "findings": ["fin_1", "fin_2"],
                "created_at": "t0",
            },
        )

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["proposal", "show", "prop_1"])

    assert result.exit_code == 0, result.output
    assert "prop_1" in result.output
    assert "fin_1, fin_2" in result.output


@pytest.mark.unit
def test_proposal_show_unknown_id_reports_404(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {"detail": "unknown proposal prop_ghost"})

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["proposal", "show", "prop_ghost"])

    assert result.exit_code != 0
    assert "unknown proposal prop_ghost" in result.output
