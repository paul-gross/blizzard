"""``completed_at`` on ``GET /api/chunks`` (issue #173) — the wire half of
:meth:`ChunkFacts.completed_at`. The derivation itself is unit-tested (pure over
``ChunkFacts``) in ``test_chunk_status_derivation.py``; this proves the hub
serializes it correctly for a terminal chunk over the real HTTP surface, and
withholds it for a non-terminal one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert as sa_insert

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema
from tests.support import assert_all_timestamps_utc, build_hub, ingest, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "12"}

# Mirrors test_chunks_api.py's own merge graph: the deliver hub node's `run: true`
# completes synchronously, so one build completion carries the chunk straight to `done`.
_MERGE_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: deliver
        fail:
          description: Incomplete.
          to: build
  deliver:
    executor: hub
    run:
      - command: "true"
    judgement:
      choices:
        success:
          description: Delivered.
          to: done
        failure:
          description: Failed to deliver.
          to: build
"""


def _claim(hub, chunk_id: str) -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert resp.status_code == 201, resp.text
    report_lease(hub, chunk_id, epoch=1, seq=1)
    return resp.json()["envelope"]["node"]["node_id"]


def _summary(hub, chunk_id: str) -> dict:  # type: ignore[no-untyped-def]
    resp = hub.client.get("/api/chunks")
    assert resp.status_code == 200, resp.text
    assert_all_timestamps_utc(resp.json())
    (summary,) = [c for c in resp.json() if c["chunk_id"] == chunk_id]
    return summary


def test_completed_at_is_null_for_a_non_terminal_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    assert _summary(hub, chunk_id)["completed_at"] is None


def test_completed_at_is_the_terminal_transitions_instant_for_done(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _MERGE_YAML}).status_code == 201
    chunk_id = ingest(hub, [_POINTER])
    build_node_id = _claim(hub, chunk_id)
    apply = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": build_node_id,
            "check_results": [{"command": "mise run test", "passed": True}],
            "artifacts": [
                {
                    "name": "work",
                    "kind": "git_commit",
                    "repo": "acme/widget",
                    "branch_name": "blizzard/ch-12",
                    "commit_hash": "abc123",
                }
            ],
        },
    )
    assert apply.status_code == 200, apply.text

    summary = _summary(hub, chunk_id)
    assert summary["status"] == "done"
    assert summary["completed_at"] is not None

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    terminal = max(detail["history"], key=lambda t: t["recorded_at"])
    assert summary["completed_at"] == terminal["recorded_at"]


def test_completed_at_is_the_stop_instant_for_stopped(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    at = datetime(2026, 1, 1, tzinfo=UTC)
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    with engine.begin() as conn:
        conn.execute(sa_insert(schema.chunk_stopped).values(chunk_id=chunk_id, stopped_at=at))

    summary = _summary(hub, chunk_id)
    assert summary["status"] == "stopped"
    assert summary["completed_at"] == "2026-01-01T00:00:00+00:00"
