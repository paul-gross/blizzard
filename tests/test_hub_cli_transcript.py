"""``blizzard hub chunk transcript list|get`` (unit tier) — read-only clients of the chunk transcript routes."""

from __future__ import annotations

import json

import httpx2
import pytest

from blizzard.hub.cli import hub as hub_group
from tests.transcript_bodies import content_body, index_body
from tests.worker_http import Bound, bind_transport

pytestmark = pytest.mark.unit

_ENV = {"BZ_HUB_URL": "http://hub.local:8421"}


def _hub(index: dict[str, object] | None = None, content: dict[str, object] | None = None) -> tuple[Bound, list[str]]:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/chunks/ch_1/transcripts" and index is not None:
            return httpx2.Response(200, json=index)
        if request.url.path == "/api/chunks/ch_1/transcripts/sg_1" and content is not None:
            return httpx2.Response(200, json=content)
        return httpx2.Response(404)

    return bind_transport(handler), seen


def test_list_prints_each_segment_and_json_prints_the_route_body() -> None:
    body = index_body("ch_1", "sg_1", "sg_2")
    bound, seen = _hub(index=body)

    listed = bound.runner.invoke(hub_group, ["chunk", "transcript", "list", "ch_1"], env=_ENV)
    raw = bound.runner.invoke(hub_group, ["chunk", "transcript", "list", "ch_1", "--json"], env=_ENV)

    assert listed.exit_code == 0, listed.output
    assert [line.split()[0] for line in listed.output.splitlines()] == ["sg_1", "sg_2"]
    assert json.loads(raw.output) == body
    assert seen == ["/api/chunks/ch_1/transcripts"] * 2


def test_get_renders_the_turns_and_json_prints_the_route_body() -> None:
    body = content_body("sg_1")
    bound, _ = _hub(content=body)

    shown = bound.runner.invoke(hub_group, ["chunk", "transcript", "get", "ch_1", "sg_1"], env=_ENV)
    raw = bound.runner.invoke(hub_group, ["chunk", "transcript", "get", "ch_1", "sg_1", "--json"], env=_ENV)

    assert shown.exit_code == 0, shown.output
    assert shown.output.splitlines()[0] == "sg_1  final"
    assert "  a.txt" in shown.output
    assert json.loads(raw.output) == body


@pytest.mark.parametrize("alias", ["show", "read"])
def test_show_and_read_resolve_to_get(alias: str) -> None:
    bound, _ = _hub(content=content_body("sg_1"))

    result = bound.runner.invoke(hub_group, ["chunk", "transcript", alias, "ch_1", "sg_1"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert result.output.startswith("sg_1  final")


def test_an_unknown_chunk_or_segment_is_refused_by_name() -> None:
    bound, _ = _hub()

    listed = bound.runner.invoke(hub_group, ["chunk", "transcript", "list", "ch_1"], env=_ENV)
    got = bound.runner.invoke(hub_group, ["chunk", "transcript", "get", "ch_1", "sg_9"], env=_ENV)

    assert listed.exit_code != 0
    assert "unknown chunk ch_1" in listed.output
    assert got.exit_code != 0
    assert "no segment sg_9" in got.output
