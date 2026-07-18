"""The hub client — driven against a fake hub via ``httpx.MockTransport``.

The runner's outbound edge is exercised with no live daemon (the tier rule for a
one-sided runner test, verification/blizzard.md): peek, the 201/409 claim split,
completion, the idempotent envelope re-read, and the chunk poll — plus a transport
failure surfacing as :class:`HubClientError`.
"""

from __future__ import annotations

import httpx
import pytest

from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError
from blizzard.runner.loop.internal.http_hub import HttpHubClient
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.route import RouteClaim


def _client(handler) -> HttpHubClient:  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(handler)
    return HttpHubClient(httpx.Client(base_url="http://hub.test", transport=transport))


@pytest.mark.unit
def test_peek_queue_parses_entries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/fleet/queue/peek"
        return httpx.Response(200, json={"entries": [{"chunk_id": "ch_1", "graph_id": "gr_1", "position": 0}]})

    peek = _client(handler).peek_queue()
    assert [e.chunk_id for e in peek.entries] == ["ch_1"]


@pytest.mark.unit
def test_claim_route_201_returns_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/fleet/routes"
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
            "route_token": "rtok_test",
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
def test_claim_route_403_is_a_paused_denial_not_a_conflict() -> None:
    """A distinct outcome from the 409 race loss (issue #44): the hub's registry has
    this runner paused and refused the claim outright."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"chunk_id": "ch_1", "runner_id": "r1", "detail": "runner is paused at the hub"}
        )

    outcome = _client(handler).claim_route(
        RouteClaim(chunk_id="ch_1", runner_id="r1", workspace_id="ws1", environment_ids=["e1"])
    )
    assert not outcome.won
    assert outcome.conflict is None
    assert outcome.denied_paused is not None and outcome.denied_paused.runner_id == "r1"


@pytest.mark.unit
def test_submit_completion_returns_apply_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/fleet/chunks/ch_1/completions"
        return httpx.Response(200, json={"outcome": "hub_node_taken", "detail": "delivering"})

    resp = _client(handler).submit_completion(
        "ch_1", CompletionSubmission(choice="pass", epoch=1, runner_id="r1", from_node_id="nd_build")
    )
    assert resp.outcome == "hub_node_taken"


@pytest.mark.unit
def test_hub_advance_posts_to_the_fleet_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/fleet/chunks/ch_1/hub-advance"
        return httpx.Response(200, json={"chunk_id": "ch_1", "status": "running", "ran": False, "detail": "busy"})

    resp = _client(handler).hub_advance("ch_1")
    assert resp.ran is False


@pytest.mark.unit
def test_get_chunk_parses_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/fleet/chunks/ch_1"
        return httpx.Response(
            200,
            json={
                "chunk_id": "ch_1",
                "graph_id": "gr_1",
                "status": "done",
                "current_node_id": "deliver",
                "latest_epoch": 1,
                "model": "claude-opus-4-8",
            },
        )

    assert _client(handler).get_chunk("ch_1").status == "done"


@pytest.mark.unit
def test_register_runner_posts_registration() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/fleet/runners"
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(201, json={"runner_id": "r1", "first_registration": True})

    _client(handler).register_runner("r1", "ws1")
    assert seen == {"runner_id": "r1", "workspace_id": "ws1"}


@pytest.mark.unit
def test_fetch_runner_paused_reads_the_derived_brake() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/fleet/runners/r1"
        return httpx.Response(
            200,
            json={
                "runner_id": "r1",
                "workspace_id": "ws1",
                "registered_at": "2026-07-13T00:00:00+00:00",
                "last_seen_at": "2026-07-13T00:00:00+00:00",
                "online": True,
                "hub_paused": True,
            },
        )

    assert _client(handler).fetch_runner_paused("r1") is True


@pytest.mark.unit
def test_transport_failure_raises_hub_client_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(HubClientError):
        _client(handler).peek_queue()


@pytest.mark.unit
def test_get_chunk_404_is_chunk_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="no such chunk")

    with pytest.raises(ChunkNotFoundError):
        _client(handler).get_chunk("ch_1")


@pytest.mark.unit
def test_get_chunk_500_is_hub_client_error_not_chunk_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(HubClientError) as exc_info:
        _client(handler).get_chunk("ch_1")
    assert not isinstance(exc_info.value, ChunkNotFoundError)


@pytest.mark.unit
def test_get_chunk_transport_failure_raises_plain_hub_client_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(HubClientError) as exc_info:
        _client(handler).get_chunk("ch_1")
    assert not isinstance(exc_info.value, ChunkNotFoundError)
