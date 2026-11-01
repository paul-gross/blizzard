"""``blizzard runner transcript list|get`` (unit tier) — pure clients of the runner's local transcript routes, reached
through the host's runner list."""

from __future__ import annotations

import json

import httpx2
import pytest

from blizzard.runner.cli import runner as runner_group
from tests.transcript_bodies import content_body, index_body
from tests.worker_http import Bound, bind_transport

pytestmark = pytest.mark.unit

_URL = "http://runner.local:8500"
_RUNNERS = {"items": [{"name": "local", "runner_id": "rn_1", "state": "serving", "served": True}]}


def _daemon(get_status: int = 200, detail: str = "") -> tuple[Bound, list[str]]:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        seen.append(path)
        if path == "/api/host/runners":
            return httpx2.Response(200, json=_RUNNERS)
        if path == "/runners/rn_1/api/chunks/ch_1/transcripts":
            return httpx2.Response(200, json=index_body("ch_1", "sg_1"))
        if path == "/runners/rn_1/api/chunks/ch_1/transcripts/sg_1":
            if get_status == 200:
                return httpx2.Response(200, json=content_body("sg_1"))
            return httpx2.Response(get_status, json={"detail": detail})
        return httpx2.Response(404, json={"detail": "unknown segment"})

    return bind_transport(handler), seen


def _run(bound: Bound, *argv: str) -> object:
    return bound.runner.invoke(runner_group, ["transcript", *argv, "--runner-url", _URL])


def test_list_prints_the_segments_this_runner_holds_and_json_prints_the_body() -> None:
    bound, seen = _daemon()

    listed = _run(bound, "list", "ch_1")
    raw = _run(bound, "list", "ch_1", "--json")

    assert listed.exit_code == 0, listed.output  # type: ignore[attr-defined]
    assert listed.output.split()[0] == "sg_1"  # type: ignore[attr-defined]
    assert json.loads(raw.output) == index_body("ch_1", "sg_1")  # type: ignore[attr-defined]
    assert "/runners/rn_1/api/chunks/ch_1/transcripts" in seen


def test_get_renders_the_turns_and_show_resolves_to_it() -> None:
    bound, _ = _daemon()

    got = _run(bound, "get", "ch_1", "sg_1")
    shown = _run(bound, "show", "ch_1", "sg_1")

    assert got.exit_code == 0, got.output  # type: ignore[attr-defined]
    assert got.output.splitlines()[0] == "sg_1  final"  # type: ignore[attr-defined]
    assert shown.output == got.output  # type: ignore[attr-defined]


def test_get_maps_an_absent_segment_and_an_unavailable_harness() -> None:
    absent = _run(_daemon(404)[0], "get", "ch_1", "sg_1")
    unavailable = _run(_daemon(503, "harness opencode is not installed")[0], "get", "ch_1", "sg_1")

    assert absent.exit_code != 0  # type: ignore[attr-defined]
    assert "holds no segment sg_1 for chunk ch_1" in absent.output  # type: ignore[attr-defined]
    assert unavailable.exit_code != 0  # type: ignore[attr-defined]
    assert "harness opencode is not installed" in unavailable.output  # type: ignore[attr-defined]
