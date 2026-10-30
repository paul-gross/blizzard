"""Operational event log — end to end (e2e tier).

A verdict-less exit exhausts the retry budget and escalates; a critical `worker-lost`
event must read back off `GET /api/events` and arrive on the SSE spine. Skipped unless
``BLIZZARD_E2E=1``.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx2
import pytest

from blizzard.foundation.hub_event_types import HubEventType
from tests.fleet_launchers import REPO, drive_until_done, mock_bin_dir, run_forge, run_hub, runner_config
from tests.fleet_world import FIXTURE_ENV, REPO_NAME, mint_world
from tests.support import free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e event log needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# A build turn that does nothing of note, whose judgement emits NO verdict() — every
# attempt fails, so the retry budget exhausts and the runner escalates.
_VERDICTLESS = "pass\n"


def _graph_yaml() -> str:
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _VERDICTLESS,
                "judgement": {
                    "prompt": _VERDICTLESS,
                    "choices": {"pass": {"description": "Green.", "to": "deliver"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "action": "test.answer",
                "with": {"choice": "landed"},
                "judgement": {
                    "choices": {
                        "landed": {"description": "Landed.", "to": "done"},
                        "conflict": {"description": "Conflict.", "to": "build"},
                        "failure": {"description": "Failed.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _sse_event_types(hub: httpx2.Client) -> list[str]:
    """The event types on the SSE spine's replay tail (``Last-Event-ID: 0`` replays the
    buffered frames on connect). The tail flushes immediately, then the live connection
    blocks — so a short read window captures it, and the ensuing ``ReadTimeout`` is
    expected, not a fault."""
    types: list[str] = []
    try:
        with hub.stream("GET", "/api/events/stream", headers={"Last-Event-ID": "0"}, timeout=3.0) as resp:
            assert resp.status_code == 200, resp.text
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    types.append(line.split(":", 1)[1].strip())
    except httpx2.ReadTimeout:
        pass  # the buffered tail drained; the live stream then blocks — expected
    return types


def test_a_verdict_less_exit_surfaces_a_critical_worker_lost_event(tmp_path: Path) -> None:
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")

    scratch = tmp_path / "scratch"
    mint_world(bin_dir, scratch)
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "event-log", "body": "the chunk"})
        assert issue.status_code == 201, issue.text
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        status = drive_until_done(config, hub, chunk_id, fenced)
        assert status == "needs_human", f"chunk did not derive needs_human (last status {status!r})"

        # (a) The critical worker-lost event reads back off the live GET /api/events.
        feed = hub.get("/api/events").json()["events"]
        lost = [e for e in feed if e["kind"] == "worker-lost" and e["chunk_id"] == chunk_id]
        assert lost, f"no worker-lost event for {chunk_id} in {feed}"
        assert lost[0]["severity"] == "critical"
        assert lost[0]["detail"]["via"] in {"advance", "reap"}

        # (b) ...and it fanned out on the SSE spine as an event-logged frame.
        assert HubEventType.EVENT_LOGGED in _sse_event_types(hub)
