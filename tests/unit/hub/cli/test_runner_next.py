"""``blizzard hub runner next`` — the next-claim explanation rendered with the client stubbed."""

from __future__ import annotations

import json
from typing import Any

import httpx2

from blizzard.hub.cli import hub as hub_group
from tests.support.hub_cli_runner import ENV, HUB_URL
from tests.support.worker_http import bind_stubs

_RUNNER = "rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6T"


def _entry(chunk_id: str, position: int, slug: str | None, **fields: Any) -> dict[str, Any]:
    urgency = {"urgency_id": f"ug_{slug}", "slug": slug} if slug else None
    return {"chunk_id": chunk_id, "graph_id": "g1", "position": position, "urgency": urgency, **fields}


def _invoke(body: dict[str, Any], *flags: str) -> tuple[str, list[str]]:
    urls: list[str] = []

    def fake(url: str, **_: Any) -> httpx2.Response:
        urls.append(url)
        return httpx2.Response(200, json=body, request=httpx2.Request("GET", HUB_URL))

    result = bind_stubs(get=fake).runner.invoke(hub_group, ["runner", "next", _RUNNER, *flags], env=ENV)
    assert result.exit_code == 0, result.output
    return result.output, urls


_BODY = {
    "next": _entry("ch_next", 3, "standard"),
    "passed": [
        _entry("ch_dep", 0, "urgent", reason="blocked", prerequisite="ch_pre", prerequisite_count=3),
        _entry("ch_gpu", 1, None, reason="ineligible"),
    ],
    "passed_total": 5,
    "unserved": 0,
    "if_holding": None,
    "held_at": [],
}


def test_prints_the_next_chunk_then_each_passed_one_with_its_urgency_and_reason() -> None:
    output, urls = _invoke(_BODY)

    assert urls == [f"{HUB_URL}/api/runners/{_RUNNER}/next-claim"]
    assert output.splitlines() == [
        "next  3  ch_next  urgency=standard",
        "  passed  0  ch_dep  urgency=urgent  blocked by ch_pre (+2 more)",
        "  passed  1  ch_gpu  urgency=-  ineligible",
        "  ... and 3 more passed",
    ]


def test_a_blocked_entry_with_one_prerequisite_names_only_it() -> None:
    body = {
        **_BODY,
        "passed": [_entry("ch_dep", 0, "urgent", reason="blocked", prerequisite="ch_pre", prerequisite_count=1)],
        "passed_total": 1,
    }

    output, _ = _invoke(body)

    assert output.splitlines()[1:] == ["  passed  0  ch_dep  urgency=urgent  blocked by ch_pre"]


def test_an_absent_next_says_nothing_is_usable() -> None:
    body = {**_BODY, "next": None, "passed": [_entry("ch_gpu", 0, "standard", reason="ineligible")], "passed_total": 1}

    output, _ = _invoke(body)

    assert output.splitlines() == [
        "next  - (nothing in claim order this runner can use)",
        "  passed  0  ch_gpu  urgency=standard  ineligible",
    ]


def test_json_prints_the_body_unchanged() -> None:
    output, _ = _invoke(_BODY, "--json")

    assert json.loads(output) == _BODY
