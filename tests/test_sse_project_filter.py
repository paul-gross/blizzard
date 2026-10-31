"""``GET /api/events/stream?project=`` — the stream a subscriber filtering by one project is sent.

A frame stamped with a chunk's project passes only for that project; a runner's frames pass when the runner serves
it, by a served set read off the event loop at subscribe and again on a ``runner-changed`` that can change it;
every other frame passes. The replay tail is filtered as the live frames are. The handler is called directly, as in
``tests/test_events_stream.py``, since a test client buffers an endless stream."""

from __future__ import annotations

import ast
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from blizzard.foundation.event_log import EventLogSeverity
from blizzard.hub.api.events import events_stream
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.events.broker import EventBroker
from tests.support import build_hub, default_project, emitted_events, ingest

pytestmark = pytest.mark.component

_MINE = ProjectId("proj_mine")
_THEIRS = ProjectId("proj_theirs")


@dataclass
class _Registry:
    """The registry read the lens makes: each runner and the projects it serves; ``reads`` counts the reads."""

    served: dict[str, set[str]] = field(default_factory=dict)
    reads: int = 0

    def runners_serving(self, project: str) -> frozenset[str]:
        self.reads += 1
        return frozenset(runner_id for runner_id, served in self.served.items() if project in served)


class _ConnectedRequest:
    """A request whose client stays connected, carrying an optional ``Last-Event-ID``."""

    def __init__(self, last_event_id: int | None = None) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace())
        self.headers = {} if last_event_id is None else {"last-event-id": str(last_event_id)}
        self.query_params: dict[str, str] = {}

    async def is_disconnected(self) -> bool:
        return False


async def _open(
    broker: EventBroker, registry: _Registry, project: ProjectId | None = _MINE, *, last_event_id: int | None = None
) -> AsyncIterator[bytes]:
    """The stream's frames, the reserved comment already read — the subscription is live from here."""
    services = SimpleNamespace(events=broker, registry=registry)
    response = await events_stream(_ConnectedRequest(last_event_id), services, project)  # type: ignore[arg-type]
    frames: AsyncIterator[bytes] = response.body_iterator  # type: ignore[assignment]
    first = await anext(frames)
    assert first.startswith(b": blizzard hub event stream")
    return frames


async def _sent(frames: AsyncIterator[bytes]) -> list[tuple[str, dict[str, object]]]:
    """Every frame the stream sends until its broker's close ends it, as ``(type, payload)``."""
    sent: list[tuple[str, dict[str, object]]] = []
    async for chunk in frames:
        text = chunk.decode() if isinstance(chunk, bytes) else str(chunk)
        if text.startswith(":"):
            continue
        lines = dict(line.split(": ", 1) for line in text.strip().splitlines())
        sent.append((lines["event"], json.loads(lines["data"])))
    return sent


async def test_a_chunk_frame_passes_only_for_its_own_project() -> None:
    broker = EventBroker()
    frames = await _open(broker, _Registry())
    broker.publish_chunk_changed("ch_mine", "ready", project_id=_MINE)
    broker.publish_chunk_changed("ch_theirs", "ready", project_id=_THEIRS)
    broker.publish_question_asked("ch_theirs", "qn_1", project_id=_THEIRS)
    broker.publish_decision_opened("ch_mine", "dec_1", project_id=_MINE)
    broker.close()

    assert [(kind, payload.get("chunk_id")) for kind, payload in await _sent(frames)] == [
        ("chunk-changed", "ch_mine"),
        ("decision-opened", "ch_mine"),
    ]


async def test_queue_changed_and_frames_naming_no_project_pass() -> None:
    broker = EventBroker()
    frames = await _open(broker, _Registry())
    broker.publish_queue_changed()
    broker.publish_chunk_changed("ch_gone", "not_ready", cause="deleted", by="operator")
    broker.publish_event_logged(severity=EventLogSeverity.INFO, kind="work-item-closed", chunk_id=None, runner_id=None)
    broker.close()

    assert [kind for kind, _ in await _sent(frames)] == ["queue-changed", "chunk-changed", "event-logged"]


async def test_a_runners_frames_pass_only_while_it_serves_the_project() -> None:
    broker = EventBroker()
    registry = _Registry(served={"rn_mine": {_MINE}, "rn_theirs": {_THEIRS}})
    frames = await _open(broker, registry)
    broker.publish_runner_changed("rn_mine", kind="heartbeat")
    broker.publish_runner_changed("rn_theirs", kind="paused", by="operator")
    broker.publish_event_logged(
        severity=EventLogSeverity.INFO, kind="runner.registered", chunk_id=None, runner_id="rn_theirs"
    )
    broker.publish_event_logged(
        severity=EventLogSeverity.INFO, kind="runner.registered", chunk_id=None, runner_id="rn_mine"
    )
    broker.close()

    assert [(kind, payload["runner_id"]) for kind, payload in await _sent(frames)] == [
        ("runner-changed", "rn_mine"),
        ("event-logged", "rn_mine"),
    ]
    assert registry.reads == 1, "a heartbeat or a pause reads no served set"


async def test_a_registration_serving_the_project_lets_that_runners_frames_through() -> None:
    broker = EventBroker()
    registry = _Registry(served={"rn_new": {_THEIRS}})
    frames = await _open(broker, registry)
    broker.publish_runner_changed("rn_new", kind="heartbeat")
    registry.served["rn_new"] = {_THEIRS, _MINE}
    broker.publish_runner_changed("rn_new", kind="registered")
    broker.publish_runner_changed("rn_new", kind="heartbeat")
    broker.close()

    assert [payload["kind"] for _, payload in await _sent(frames)] == ["registered", "heartbeat"]
    assert registry.reads == 2


async def test_the_replay_tail_is_filtered_as_the_live_frames_are() -> None:
    broker = EventBroker()
    first = broker.publish_chunk_changed("ch_mine", "ready", project_id=_MINE)
    broker.publish_chunk_changed("ch_theirs", "ready", project_id=_THEIRS)
    broker.publish_chunk_changed("ch_mine", "running", project_id=_MINE)
    broker.publish_runner_changed("rn_theirs", kind="heartbeat")
    broker.close()

    frames = await _open(broker, _Registry(served={"rn_theirs": {_THEIRS}}), last_event_id=first)

    assert [(payload["chunk_id"], payload["status"]) for _, payload in await _sent(frames)] == [("ch_mine", "running")]


async def test_without_a_project_every_frame_is_sent_and_no_served_set_is_read() -> None:
    broker = EventBroker()
    registry = _Registry(served={"rn_theirs": {_THEIRS}})
    frames = await _open(broker, registry, None)
    broker.publish_chunk_changed("ch_theirs", "ready", project_id=_THEIRS)
    broker.publish_runner_changed("rn_theirs", kind="registered")
    broker.close()

    assert len(await _sent(frames)) == 2
    assert registry.reads == 0


def test_an_unknown_project_is_refused_before_the_stream_opens(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    response = hub.client.get("/api/events/stream", params={"project": "no-such-project"})
    assert response.status_code == 422, response.text


def test_an_ingested_chunks_frames_carry_its_project(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    frames = [json.loads(e["data"]) for e in emitted_events(hub) if e["event"] == "chunk-changed"]
    assert frames, "the ingest published its chunk's frames"
    assert {frame["project_id"] for frame in frames if frame["chunk_id"] == chunk_id} == {default_project(hub.engine)}


#: The broker's chunk-scoped publishers: each frame names a chunk, so each publisher passes the chunk's project.
_CHUNK_SCOPED = frozenset(
    {
        "publish_chunk_changed",
        "publish_question_asked",
        "publish_question_answered",
        "publish_decision_opened",
        "publish_decision_resolved",
        "publish_event_logged",
    }
)


def test_every_chunk_scoped_publish_site_stamps_the_chunks_project() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "blizzard" / "hub"
    unstamped: list[str] = []
    sites = 0
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _CHUNK_SCOPED
                and not (
                    isinstance(node.func.value, ast.Name) and node.func.value.id == "self" and path.name == "broker.py"
                )
            ):
                sites += 1
                if not any(keyword.arg == "project_id" for keyword in node.keywords):
                    unstamped.append(f"{path.relative_to(root)}:{node.lineno}")
    assert sites >= 8, "the scan found the publish sites"
    assert unstamped == []


async def test_from_latest_sends_only_the_projects_frames_published_after_it_joined() -> None:
    broker = EventBroker()
    broker.publish_chunk_changed("ch_old", "ready", project_id=_MINE)
    request = _ConnectedRequest()
    request.query_params["from"] = "latest"
    services = SimpleNamespace(events=broker, registry=_Registry())
    response = await events_stream(request, services, _MINE)  # type: ignore[arg-type]
    frames: AsyncIterator[bytes] = response.body_iterator  # type: ignore[assignment]
    assert (await anext(frames)).startswith(b": blizzard hub event stream")
    broker.publish_chunk_changed("ch_mine", "ready", project_id=_MINE)
    broker.publish_chunk_changed("ch_theirs", "ready", project_id=_THEIRS)
    broker.close()

    assert [payload.get("chunk_id") for _, payload in await _sent(frames)] == ["ch_mine"]


async def _route(query: dict[str, str], header: str | None = None, *, broker: EventBroker) -> AsyncIterator[bytes]:
    request = _ConnectedRequest(None if header is None else int(header))
    request.query_params.update(query)
    services = SimpleNamespace(events=broker, registry=_Registry())
    response = await events_stream(request, services, None)  # type: ignore[arg-type]
    return response.body_iterator  # type: ignore[return-value]


@pytest.mark.parametrize("query", [{"from": "earliest"}, {"from": "latest", "last_event_id": "1"}])
async def test_the_hub_route_refuses_an_unknown_from_and_from_latest_with_a_query_cursor(query: dict[str, str]) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _route(query, broker=EventBroker())
    assert exc_info.value.status_code == 422


async def test_the_hub_route_from_latest_sends_the_comment_then_only_later_events() -> None:
    broker = EventBroker()
    broker.publish_queue_changed()
    frames = await _route({"from": "latest"}, broker=broker)
    assert (await anext(frames)).startswith(b": blizzard hub event stream")
    broker.publish_chunk_changed("ch_new", "ready", project_id=_MINE)
    broker.close()

    assert [kind for kind, _ in await _sent(frames)] == ["chunk-changed"]


async def test_the_hub_route_from_latest_resumes_from_a_last_event_id_header() -> None:
    broker = EventBroker()
    broker.publish_queue_changed()
    broker.publish_chunk_changed("ch_two", "ready", project_id=_MINE)
    frames = await _route({"from": "latest"}, "1", broker=broker)
    assert (await anext(frames)).startswith(b": blizzard hub event stream")
    broker.close()

    assert [payload.get("chunk_id") for _, payload in await _sent(frames)] == ["ch_two"]
