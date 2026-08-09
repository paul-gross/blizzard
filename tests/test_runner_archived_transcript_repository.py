"""``HttpArchivedTranscriptRepository`` — driven against a fake hub via ``httpx.MockTransport``
(blizzard#249, D4). The seam's whole contract: every outcome, including a transport failure,
reaches the caller as a value on :class:`ArchivedTranscript`, never a raised exception. Also
pins that the request carries the runner's own auth header — AC3's transport-layer half."""

from __future__ import annotations

import httpx
import pytest

from blizzard.runner.transcripts.internal import http_archived_transcript_repository as adapter_module
from blizzard.runner.transcripts.internal.http_archived_transcript_repository import (
    HttpArchivedTranscriptRepository,
)


def _repo(handler) -> HttpArchivedTranscriptRepository:  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(handler)
    client = httpx.Client(base_url="http://hub.test", transport=transport, headers={"Authorization": "Bearer tok"})
    return HttpArchivedTranscriptRepository(client)


@pytest.mark.unit
def test_found_turns_are_projected_and_the_request_is_authenticated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/fleet/chunks/ch_1/transcript-segments"
        assert dict(request.url.params) == {"node_id": "nd_build", "epoch": "1"}
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(
            200,
            json={
                "chunk_id": "ch_1",
                "node_id": "nd_build",
                "epoch": 1,
                "final": True,
                "truncated": False,
                "turns": [
                    {
                        "index": 0,
                        "kind": "asst",
                        "timestamp": None,
                        "text": "hi",
                        "tool": None,
                        "thinking_redacted": False,
                        "sidechain": None,
                        "truncated": False,
                    }
                ],
            },
        )

    result = _repo(handler).read_turns(chunk_id="ch_1", node_id="nd_build", epoch=1)

    assert result.status == "found"
    assert [t.text for t in result.turns] == ["hi"]
    assert result.dropped == 0
    assert result.truncated is False


@pytest.mark.unit
def test_thinking_turns_and_sidechains_are_dropped_and_counted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "chunk_id": "ch_1",
                "node_id": "nd_build",
                "epoch": 1,
                "final": True,
                "truncated": False,
                "turns": [
                    {
                        "index": 0,
                        "kind": "thinking",
                        "timestamp": None,
                        "text": "",
                        "tool": None,
                        "thinking_redacted": True,
                        "sidechain": None,
                        "truncated": False,
                    },
                    {
                        "index": 1,
                        "kind": "tool",
                        "timestamp": None,
                        "text": "",
                        "tool": {
                            "name": "Task",
                            "input": {},
                            "input_unparsed": None,
                            "input_shape": "object",
                            "tool_use_id": "t1",
                            "output": "done",
                            "output_truncated": False,
                        },
                        "thinking_redacted": False,
                        "sidechain": {
                            "agent_id": "agent-1",
                            "agent_type": "explorer",
                            "link": "resolved",
                            "turns": [
                                {
                                    "index": 0,
                                    "kind": "asst",
                                    "timestamp": None,
                                    "text": "nested",
                                    "tool": None,
                                    "thinking_redacted": False,
                                    "sidechain": None,
                                    "truncated": False,
                                }
                            ],
                        },
                        "truncated": False,
                    },
                ],
            },
        )

    result = _repo(handler).read_turns(chunk_id="ch_1", node_id="nd_build", epoch=1)

    assert result.status == "found"
    assert [t.kind for t in result.turns] == ["tool"]  # the spawning call kept
    assert result.dropped == 2  # the thinking turn + the one nested sidechain turn


@pytest.mark.unit
def test_empty_turns_is_the_hub_holds_nothing_outcome() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "chunk_id": "ch_1",
                "node_id": "nd_build",
                "epoch": 1,
                "final": False,
                "truncated": False,
                "turns": [],
            },
        )

    result = _repo(handler).read_turns(chunk_id="ch_1", node_id="nd_build", epoch=1)
    assert result.status == "empty"
    assert result.turns == []


@pytest.mark.unit
def test_all_cap_rejected_records_read_as_found_and_truncated_not_empty() -> None:
    """The hub's own truncation signal on an otherwise-empty lease must not collapse
    into the same outcome as a lease it holds nothing for."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "chunk_id": "ch_1",
                "node_id": "nd_build",
                "epoch": 1,
                "final": False,
                "truncated": True,
                "turns": [],
            },
        )

    result = _repo(handler).read_turns(chunk_id="ch_1", node_id="nd_build", epoch=1)
    assert result.status == "found"
    assert result.turns == []
    assert result.truncated is True


@pytest.mark.unit
def test_dropped_turns_counts_only_the_surviving_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """A thinking turn dropped ahead of a window the recency cap discards entirely must
    not inflate the count for the window actually rendered."""
    monkeypatch.setattr(adapter_module, "MAX_TURNS", 2)

    def turn(index: int, kind: str, text: str = "") -> dict[str, object]:
        return {
            "index": index,
            "kind": kind,
            "timestamp": None,
            "text": text,
            "tool": None,
            "thinking_redacted": kind == "thinking",
            "sidechain": None,
            "truncated": False,
        }

    def handler(request: httpx.Request) -> httpx.Response:
        turns = [
            turn(0, "thinking"),
            turn(1, "asst", "msg-0"),  # dropped out of the window by the MAX_TURNS=2 cap
            turn(2, "thinking"),
            turn(3, "asst", "msg-1"),
            turn(4, "asst", "msg-2"),
        ]
        return httpx.Response(
            200,
            json={
                "chunk_id": "ch_1",
                "node_id": "nd_build",
                "epoch": 1,
                "final": True,
                "truncated": False,
                "turns": turns,
            },
        )

    result = _repo(handler).read_turns(chunk_id="ch_1", node_id="nd_build", epoch=1)

    assert [t.text for t in result.turns] == ["msg-1", "msg-2"]
    assert result.dropped == 1  # only the thinking turn ahead of msg-1, not the one ahead of msg-0


@pytest.mark.unit
@pytest.mark.parametrize("status", [401, 403])
def test_401_and_403_are_refused_not_unreachable(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "no"})

    result = _repo(handler).read_turns(chunk_id="ch_1", node_id="nd_build", epoch=1)
    assert result.status == "refused"


@pytest.mark.unit
def test_a_transport_failure_is_unreachable_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    result = _repo(handler).read_turns(chunk_id="ch_1", node_id="nd_build", epoch=1)
    assert result.status == "unreachable"


@pytest.mark.unit
def test_a_500_is_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    result = _repo(handler).read_turns(chunk_id="ch_1", node_id="nd_build", epoch=1)
    assert result.status == "unreachable"


@pytest.mark.unit
def test_a_malformed_body_is_unreachable_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    result = _repo(handler).read_turns(chunk_id="ch_1", node_id="nd_build", epoch=1)
    assert result.status == "unreachable"


@pytest.mark.unit
def test_the_max_turns_cap_reindexes_from_zero_and_flags_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "MAX_TURNS", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        turns = [
            {
                "index": i,
                "kind": "asst",
                "timestamp": None,
                "text": f"msg-{i}",
                "tool": None,
                "thinking_redacted": False,
                "sidechain": None,
                "truncated": False,
            }
            for i in range(4)
        ]
        return httpx.Response(
            200,
            json={
                "chunk_id": "ch_1",
                "node_id": "nd_build",
                "epoch": 1,
                "final": True,
                "truncated": False,
                "turns": turns,
            },
        )

    result = _repo(handler).read_turns(chunk_id="ch_1", node_id="nd_build", epoch=1)

    assert result.truncated is True
    assert [t.text for t in result.turns] == ["msg-2", "msg-3"]
    assert [t.index for t in result.turns] == [0, 1]
