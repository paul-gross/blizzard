"""Escalation at the hub — needs_human and its supersession (component tier) — MVP criterion 6.

The runner reports ``escalation.recorded`` up when a node's retries exhaust (D-009);
this pins the hub behavior that fact drives:

* the chunk derives **needs_human** (highest live precedence after terminal, D-067);
* the open escalation surfaces the **takeover command** so the parked session is
  resumable — the pasteable ``cd <workdir> && <harness resume>`` (harness-adapters.md);
* a later **lease mint** (a requeue/takeover) **closes it by supersession** — no
  resolution fact — flipping the chunk back off needs_human (D-067).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tests.support import build_hub

pytestmark = pytest.mark.component

_POINTER = {"provider": "github", "url": "http://forge.local/repos/acme/widget/issues/9"}
_TAKEOVER = "cd /ws/e1 && mock-claude-code --resume sess-abc"

_GRAPH_YAML = """
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
    retries:
      max: 2
      exhausted: escalate
  deliver:
    executor: hub
    mode: merge-to-main
"""


def _claim(hub) -> str:  # type: ignore[no-untyped-def]
    assert hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"pointers": [_POINTER]}).json()["chunk_id"]
    claim = hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    return chunk_id


def test_escalation_derives_needs_human_and_surfaces_takeover(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"

    hub.clock.advance(timedelta(minutes=5))  # the escalation lands after the claim's lease
    resp = hub.client.post(
        f"/api/chunks/{chunk_id}/escalations",
        json={"epoch": 1, "runner_id": "r1", "takeover_command": _TAKEOVER},
    )
    assert resp.status_code == 202, resp.text

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "needs_human"
    assert detail["escalation"] is not None
    assert detail["escalation"]["epoch"] == 1
    assert detail["escalation"]["takeover_command"] == _TAKEOVER


def test_requeue_lease_mint_closes_escalation_by_supersession(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)

    hub.clock.advance(timedelta(minutes=5))
    hub.client.post(
        f"/api/chunks/{chunk_id}/escalations",
        json={"epoch": 1, "runner_id": "r1", "takeover_command": _TAKEOVER},
    )
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "needs_human"

    # A requeue mints a fresh lease AFTER the escalation — supersession, no resolution.
    hub.clock.advance(timedelta(minutes=5))
    assert hub.client.post(f"/api/chunks/{chunk_id}/leases", json={"epoch": 2, "runner_id": "r1"}).status_code == 202

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "running"  # back on the route, escalation closed
    assert detail["escalation"] is None


def test_escalation_on_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post(
        "/api/chunks/ch_missing/escalations",
        json={"epoch": 1, "runner_id": "r1", "takeover_command": _TAKEOVER},
    )
    assert resp.status_code == 404
