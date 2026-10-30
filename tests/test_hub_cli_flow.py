"""``blizzard hub flow list`` / ``show`` and ``chunk show``'s place lines against the real flow routes (component
tier), over two chunks in one line at ``merge``: ``--json`` prints each route's body, the text names every place as the
hub judged it, and ``--record`` pages the record to its ``--limit``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from click.testing import Result

from blizzard.hub.cli import hub as hub_group
from tests.test_flow_routes import _Line
from tests.worker_http import Bound, bind_stubs

pytestmark = pytest.mark.component

_HUB_URL = "http://hub.local:8421"


@pytest.fixture
def line(tmp_path: Path) -> _Line:
    built = _Line(tmp_path)
    built.world.poll_due()
    return built


def _relay(line: _Line, calls: list[dict[str, str]]) -> Bound:
    def fake_get(url: str, *, params: dict[str, str] | None = None, timeout: float, **_: object) -> httpx2.Response:
        calls.append(dict(params or {}))
        return line.client.get(url, params=params)

    return bind_stubs(get=fake_get)


def _invoke(line: _Line, *args: str, calls: list[dict[str, str]] | None = None) -> Result:
    result = _relay(line, [] if calls is None else calls).runner.invoke(
        hub_group, list(args), env={"BZ_HUB_URL": _HUB_URL}
    )
    assert result.exit_code == 0, result.output
    return result


def _json(result: Result) -> Any:
    return json.loads(result.output)


def test_flow_list_json_is_the_active_routes_body_and_all_drops_the_filter(line: _Line) -> None:
    calls: list[dict[str, str]] = []

    assert (
        _json(_invoke(line, "flow", "list", "--json", calls=calls))
        == line.client.get("/api/flows", params={"active": True}).json()
    )
    assert _json(_invoke(line, "flow", "list", "--all", "--json", calls=calls)) == line.client.get("/api/flows").json()
    assert calls == [{"active": "true"}, {}]


def test_flow_list_prints_each_place_the_line_holds(line: _Line) -> None:
    lines = _invoke(line, "flow", "list").output.splitlines()

    assert lines[0].startswith(f"{line.flow_id}  ")
    assert lines[0].endswith("turn=-")
    assert lines[1:3] == [f"  {line.first}  next  position 1", f"  {line.second}  waiting  position 2"]


def test_flow_show_json_is_the_flows_body(line: _Line) -> None:
    result = _invoke(line, "flow", "show", line.flow_id, "--json")

    assert _json(result) == line.client.get(f"/api/flows/{line.flow_id}").json()


def test_flow_show_refuses_an_unknown_flow(line: _Line) -> None:
    result = _relay(line, []).runner.invoke(hub_group, ["flow", "show", "flw_missing"], env={"BZ_HUB_URL": _HUB_URL})

    assert result.exit_code != 0
    assert "unknown flow flw_missing" in result.output


def test_flow_show_record_json_is_the_record_page(line: _Line) -> None:
    result = _invoke(line, "flow", "show", line.flow_id, "--record", "--limit", "4", "--json")

    assert _json(result) == line.client.get(f"/api/flows/{line.flow_id}/record", params={"limit": 4}).json()


def test_flow_show_record_pages_on_next_before_until_the_limit_is_read() -> None:
    pages = {
        None: {"facts": [_fact(9), _fact(8)], "next_before": 8},
        "8": {"facts": [_fact(7)], "next_before": 7},
    }
    calls: list[dict[str, str]] = []

    def fake_get(url: str, *, params: dict[str, str], timeout: float, **_: object) -> httpx2.Response:
        calls.append(dict(params))
        return httpx2.Response(200, json=pages[params.get("before")], request=httpx2.Request("GET", url))

    result = bind_stubs(get=fake_get).runner.invoke(
        hub_group, ["flow", "show", "flw_1", "--record", "--limit", "3", "--json"], env={"BZ_HUB_URL": _HUB_URL}
    )

    assert result.exit_code == 0, result.output
    assert calls == [{"limit": "3"}, {"limit": "1", "before": "8"}]
    assert _json(result) == {"facts": [_fact(9), _fact(8), _fact(7)], "next_before": 7}


def _fact(fact_id: int) -> dict[str, object]:
    return {"id": fact_id, "flow_id": "flw_1", "submission_id": "sub_1", "chunk_id": "ch_1", "kind": "landed"}


def test_flow_show_record_prints_one_line_per_entry_newest_first(line: _Line) -> None:
    whole = line.client.get(f"/api/flows/{line.flow_id}/record").json()["facts"]

    text = _invoke(line, "flow", "show", line.flow_id, "--record", "--limit", "1").output.splitlines()

    newest = whole[0]
    assert text == [f"{newest['id']}  {newest['at']}  {newest['kind']}  {newest['chunk_id']}  outcome=pending"]


def test_chunk_show_prints_one_place_line_per_flow_under_its_status(line: _Line) -> None:
    lines = _invoke(line, "chunk", "show", line.second).output.splitlines()

    assert lines[0].startswith(f"{line.second}  status=delivering")
    assert lines[1].startswith("  on ")
    assert lines[1].endswith(f"({line.flow_id}): waiting  position 2")
    assert _json(_invoke(line, "chunk", "show", line.second, "--json")) == line.world.detail(line.second)
