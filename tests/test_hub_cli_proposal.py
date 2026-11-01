"""``blizzard hub garden-proposal list|get`` (unit tier) — pure clients of the
garden-proposal routes, driven here with the client stubbed, the
``tests/test_hub_cli_scope.py`` shape."""

from __future__ import annotations

import httpx2
import pytest

from blizzard.hub.cli import hub as hub_group
from tests.worker_http import bind_stubs


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
            request = httpx2.Request("GET", "http://hub.local")
            raise httpx2.HTTPStatusError(
                "error", request=request, response=httpx2.Response(self.status_code, request=request)
            )


@pytest.mark.unit
def test_garden_proposal_list_prints_each_row() -> None:
    def fake_get(url: str, *, timeout: float, params: object | None = None) -> _FakeResponse:
        return _FakeResponse(
            200,
            {
                "proposals": [
                    {
                        "proposal_id": "gprop_1",
                        "origin": "routine-run",
                        "routine_name": "nightly",
                        "class": "fix-the-source",
                        "title": "Author a docstring standard",
                        "body": "the case",
                        "findings": ["fin_1"],
                        "created_at": "t0",
                    }
                ],
                "next_cursor": None,
            },
        )

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["garden-proposal", "list"])

    assert result.exit_code == 0, result.output
    assert "gprop_1" in result.output
    assert "fix-the-source" in result.output


@pytest.mark.unit
def test_garden_proposal_list_drains_every_page() -> None:
    page_1 = {
        "proposals": [
            {
                "proposal_id": "gprop_1",
                "origin": "routine-run",
                "routine_name": "nightly",
                "class": "fix-the-source",
                "title": "Author a docstring standard",
                "body": "the case",
                "findings": ["fin_1"],
                "created_at": "t0",
            }
        ],
        "next_cursor": "cursor-1",
    }
    page_2 = {
        "proposals": [
            {
                "proposal_id": "gprop_2",
                "origin": "routine-run",
                "routine_name": "nightly",
                "class": "fix-the-source",
                "title": "Author a second standard",
                "body": "the other case",
                "findings": ["fin_2"],
                "created_at": "t1",
            }
        ],
        "next_cursor": None,
    }

    def fake_get(url: str, *, timeout: float, params: dict[str, str] | None = None) -> _FakeResponse:
        if params is not None and params.get("cursor") is not None:
            return _FakeResponse(200, page_2)
        return _FakeResponse(200, page_1)

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["garden-proposal", "list"])

    assert result.exit_code == 0, result.output
    assert "gprop_1" in result.output
    assert "gprop_2" in result.output


@pytest.mark.unit
def test_garden_proposal_show_renders_the_detail() -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            {
                "proposal_id": "gprop_1",
                "origin": "routine-run",
                "routine_name": "nightly",
                "class": "fix-the-source",
                "title": "Author a docstring standard",
                "body": "the case",
                "findings": ["fin_1", "fin_2"],
                "created_at": "t0",
            },
        )

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["garden-proposal", "get", "gprop_1"])

    assert result.exit_code == 0, result.output
    assert "gprop_1" in result.output
    assert "fin_1, fin_2" in result.output


@pytest.mark.unit
def test_garden_proposal_show_omits_the_findings_line_when_there_are_none() -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            {
                "proposal_id": "gprop_1",
                "origin": "routine-run",
                "routine_name": "nightly",
                "class": "fix-the-source",
                "title": "Author a docstring standard",
                "body": "the case",
                "findings": [],
                "created_at": "t0",
            },
        )

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["garden-proposal", "get", "gprop_1"])

    assert result.exit_code == 0, result.output
    assert "gprop_1" in result.output
    assert "findings:" not in result.output


@pytest.mark.unit
def test_garden_proposal_show_unknown_id_reports_404() -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {"detail": "unknown garden proposal gprop_ghost"})

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["garden-proposal", "get", "gprop_ghost"])

    assert result.exit_code != 0
    assert "unknown garden proposal gprop_ghost" in result.output


def _row(origin: str, routine_name: str | None, created_by: str | None) -> dict[str, object]:
    return {
        "proposal_id": "gprop_1",
        "origin": origin,
        "routine_name": routine_name,
        "created_by": created_by,
        "class": "fix-the-source",
        "title": "Author a docstring standard",
        "body": "the case",
        "findings": [],
        "created_at": "t0",
    }


def _render(row: dict[str, object], verb: list[str]) -> str:
    payload = {"proposals": [row], "next_cursor": None} if verb == ["list"] else row

    def fake_get(url: str, *, timeout: float, params: dict[str, str] | None = None) -> _FakeResponse:
        return _FakeResponse(200, payload)

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["garden-proposal", *verb])
    assert result.exit_code == 0, result.output
    return result.output


@pytest.mark.unit
@pytest.mark.parametrize("verb", [["list"], ["get", "gprop_1"]])
def test_an_operator_row_with_a_routine_renders_routine_and_created_by(verb: list[str]) -> None:
    output = _render(_row("operator", "nightly", "paul"), verb)

    assert "origin=operator  routine=nightly  created_by=paul" in output


@pytest.mark.unit
@pytest.mark.parametrize("verb", [["list"], ["get", "gprop_1"]])
def test_an_operator_row_without_a_routine_renders_no_routine_and_no_none(verb: list[str]) -> None:
    output = _render(_row("operator", None, "paul"), verb)

    assert "origin=operator  created_by=paul" in output
    assert "routine=" not in output
    assert "None" not in output


@pytest.mark.unit
@pytest.mark.parametrize("verb", [["list"], ["get", "gprop_1"]])
def test_a_routine_run_row_renders_routine_and_no_created_by(monkeypatch: pytest.MonkeyPatch, verb: list[str]) -> None:
    output = _render(_row("routine-run", "nightly", None), verb)

    assert "origin=routine-run  routine=nightly" in output
    assert "created_by" not in output
