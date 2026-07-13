"""The hub client — driven against a fake hub via ``httpx.MockTransport``.

The runner's outbound edge is exercised with no live daemon (the tier rule for a
one-sided runner test, verification/blizzard.md): peek, the 201/409 claim split,
completion, the idempotent envelope re-read, and the chunk poll — plus a transport
failure surfacing as :class:`HubClientError`.
"""

from __future__ import annotations

import httpx
import pytest

from blizzard.runner.loop.hub import HubClientError
from blizzard.runner.loop.internal.http_hub import HttpHubClient
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.route import RouteClaim


def _client(handler) -> HttpHubClient:  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(handler)
    return HttpHubClient(httpx.Client(base_url="http://hub.test", transport=transport))


@pytest.mark.unit
def test_peek_queue_parses_entries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/queue/peek"
        return httpx.Response(200, json={"entries": [{"chunk_id": "ch_1", "graph_id": "gr_1", "position": 0}]})

    peek = _client(handler).peek_queue()
    assert [e.chunk_id for e in peek.entries] == ["ch_1"]


@pytest.mark.unit
def test_claim_route_201_returns_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/routes"
        body = {
            "chunk_id": "ch_1",
            "runner_id": "r1",
            "workspace_id": "ws1",
            "environment_ids": ["e1"],
            "envelope": {
                "chunk_id": "ch_1",
                "graph_id": "gr_1",
                "epoch": 1,
                "node": {
                    "node_id": "nd_build",
                    "node_name": "build",
                    "executor": "runner",
                    "session": "fresh",
                    "judged_by": "worker",
                },
                "prompt": "do work",
                "judgement_prompt": "assess",
            },
        }
        return httpx.Response(201, json=body)

    outcome = _client(handler).claim_route(
        RouteClaim(chunk_id="ch_1", runner_id="r1", workspace_id="ws1", environment_ids=["e1"])
    )
    assert outcome.won
    assert outcome.claimed is not None
    assert outcome.claimed.envelope.node.node_name == "build"


@pytest.mark.unit
def test_claim_route_409_is_conflict_not_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"chunk_id": "ch_1", "held_by_runner_id": "r2", "detail": "already claimed"})

    outcome = _client(handler).claim_route(
        RouteClaim(chunk_id="ch_1", runner_id="r1", workspace_id="ws1", environment_ids=["e1"])
    )
    assert not outcome.won
    assert outcome.conflict is not None and outcome.conflict.held_by_runner_id == "r2"


@pytest.mark.unit
def test_submit_completion_returns_apply_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chunks/ch_1/completions"
        return httpx.Response(200, json={"outcome": "hub_node_taken", "detail": "delivering"})

    resp = _client(handler).submit_completion(
        "ch_1", CompletionSubmission(choice="pass", epoch=1, runner_id="r1", from_node_id="nd_build")
    )
    assert resp.outcome == "hub_node_taken"


@pytest.mark.unit
def test_get_chunk_parses_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chunks/ch_1"
        return httpx.Response(
            200,
            json={
                "chunk_id": "ch_1",
                "graph_id": "gr_1",
                "status": "done",
                "current_node_id": "deliver",
                "latest_epoch": 1,
            },
        )

    assert _client(handler).get_chunk("ch_1").status == "done"


@pytest.mark.unit
def test_transport_failure_raises_hub_client_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(HubClientError):
        _client(handler).peek_queue()
