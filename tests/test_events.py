"""The event broker and the SSE stream (D-067) — chunk-changed emission (component tier)."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.events.broker import EventBroker
from tests.support import build_hub

pytestmark = pytest.mark.component

_POINTER = {"provider": "github", "url": "http://forge.local/repos/acme/widget/issues/5"}


def test_broker_frames_chunk_changed_events() -> None:
    broker = EventBroker()
    broker.publish_chunk_changed("ch_1", "ready")
    broker.publish_chunk_changed("ch_1", "running")
    frames = broker.snapshot()
    assert len(frames) == 2
    assert frames[0].startswith("event: chunk-changed\n")
    assert '"chunk_id": "ch_1"' in frames[0]
    assert '"status": "running"' in frames[1]


def test_lifecycle_publishes_events_and_stream_replays_them(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"pointers": [_POINTER]}).json()["chunk_id"]
    hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )

    # The mutating routes published chunk-changed; the SSE stream is a terminating
    # replay of the recent buffer (stub-level, additive — ORCHESTRATION.md).
    stream = hub.client.get("/api/events/stream")
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    text = stream.text
    assert text.startswith(": blizzard hub event stream")
    assert "event: chunk-changed" in text
    assert chunk_id in text
    assert '"status": "running"' in text
