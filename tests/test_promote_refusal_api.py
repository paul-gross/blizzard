"""``POST /chunks/{id}/promote`` on a terminal chunk (component tier): a never-promoted chunk that
was stopped or completed is refused with 409 and gains no promotion or queue position; an
already-promoted one replays as a 202 that writes nothing."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub, ingest

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "41"}


@pytest.mark.parametrize("verb", ["stop", "complete"])
def test_promote_refuses_a_never_promoted_terminal_chunk(tmp_path: Path, verb: str) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    assert hub.client.post(f"/api/chunks/{chunk_id}/{verb}", json={"by": "alice"}).status_code == 202

    resp = hub.client.post(f"/api/chunks/{chunk_id}/promote")

    assert resp.status_code == 409
    expected = "stopped" if verb == "stop" else "done"
    assert resp.json()["detail"] == f"chunk {chunk_id} is {expected}, not promotable"
    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None and facts.promoted is False
    assert hub.services.chunks.queue.queue_positions([chunk_id]) == {}


def test_promote_replays_on_an_already_promoted_terminal_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=True)
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "alice"}).status_code == 202

    resp = hub.client.post(f"/api/chunks/{chunk_id}/promote")

    assert resp.status_code == 202
    assert resp.json()["status"] == "stopped"
