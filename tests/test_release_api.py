"""``POST /api/fleet/chunks/{chunk_id}/release`` at the wired hub (component tier) — each answer the
route gives, the chunk re-deriving ``ready`` at its node after an accepted release, and the feed's
``released`` entry naming the holder and the reason."""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest

from blizzard.foundation.hub_event_types import HubEventType
from blizzard.hub.domain.execution.auth.route import ROUTE_TOKEN_ENFORCE
from tests.support import HubHarness, build_hub, emitted_events, pointer_token

pytestmark = pytest.mark.component

_GRAPH_YAML = """
name: two-step
entry: plan
nodes:
  plan:
    executor: runner
    prompt: |
      Plan the change.
    judgement:
      prompt: |
        Assess the plan.
      choices:
        pass:
          description: Planned.
          to: build
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Built.
          to: done
"""


def _claimed(hub: HubHarness) -> tuple[str, str]:
    """Ingest, promote, and claim a chunk for ``r1``; return (chunk_id, route token)."""
    assert hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}).status_code == 201
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "9"})]}
    ).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    return chunk_id, str(claim.json()["route_token"])


def _release(
    hub: HubHarness,
    chunk_id: str,
    *,
    epoch: int = 1,
    route_token: str | None,
    runner_id: str = "r1",
    reason: str = "throttled",
) -> httpx2.Response:
    # The harness authenticates as the body's `runner_id`; the hub itself never reads it.
    body: dict[str, object] = {"epoch": epoch, "reason": reason, "runner_id": runner_id}
    if route_token is not None:
        body["route_token"] = route_token
    return hub.client.post(f"/api/fleet/chunks/{chunk_id}/release", json=body)


def _status(hub: HubHarness, chunk_id: str) -> str:
    return str(hub.client.get(f"/api/chunks/{chunk_id}").json()["status"])


def test_an_accepted_release_answers_200_and_the_chunk_reads_ready(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, token = _claimed(hub)

    resp = _release(hub, chunk_id, route_token=token)

    assert resp.status_code == 200, resp.text
    assert resp.json()["chunk_id"] == chunk_id
    assert isinstance(resp.json()["released_id"], int)
    assert _status(hub, chunk_id) == "ready"


def test_a_stale_epoch_is_refused_409_naming_stale(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, token = _claimed(hub)

    resp = _release(hub, chunk_id, epoch=0, route_token=token)

    assert resp.status_code == 409, resp.text
    assert resp.json()["reason"] == "stale"
    assert _status(hub, chunk_id) == "running"


def test_a_caller_not_holding_the_route_is_refused_403(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, token = _claimed(hub)

    resp = _release(hub, chunk_id, route_token=token, runner_id="r2")

    assert resp.status_code == 403, resp.text
    assert _status(hub, chunk_id) == "running"


@pytest.mark.parametrize("reason", ["", "x" * 65])
def test_a_reason_outside_its_bounds_is_refused_422(tmp_path: Path, reason: str) -> None:
    hub = build_hub(tmp_path)
    chunk_id, token = _claimed(hub)

    resp = _release(hub, chunk_id, route_token=token, reason=reason)

    assert resp.status_code == 422, resp.text
    assert _status(hub, chunk_id) == "running"


def test_an_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    assert _release(hub, "ch_nope", route_token=None).status_code == 404


def test_a_mismatched_route_token_is_refused_403_under_enforce(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, _ = _claimed(hub)

    resp = _release(hub, chunk_id, route_token="not-the-token")

    assert resp.status_code == 403, resp.text
    assert _status(hub, chunk_id) == "running"


def test_a_mismatched_route_token_proceeds_under_warn(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _ = _claimed(hub)

    resp = _release(hub, chunk_id, route_token="not-the-token")

    assert resp.status_code == 200, resp.text
    assert _status(hub, chunk_id) == "ready"


def test_the_feed_reads_the_release_as_released_by_its_runner_with_its_reason(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, token = _claimed(hub)
    released_id = _release(hub, chunk_id, route_token=token, reason="usage-limited").json()["released_id"]

    rows = hub.client.get("/api/activity").json()["activity"]

    entry = next(row for row in rows if row["key"] == f"route_released:{released_id}")
    assert entry["cause"] == "released"
    assert entry["runner_id"] == "r1"
    assert entry["reason"] == "usage-limited"


def test_the_live_frame_names_the_releasing_runner_and_its_reason(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, token = _claimed(hub)

    released_id = _release(hub, chunk_id, route_token=token).json()["released_id"]

    events = emitted_events(hub)
    frame = [json.loads(e["data"]) for e in events if e["event"] == HubEventType.CHUNK_CHANGED][-1]
    assert frame["cause"] == "released"
    assert frame["status"] == "ready"
    assert frame["runner_id"] == "r1"
    assert frame["reason"] == "throttled"
    assert frame["key"] == f"route_released:{released_id}"
    assert events[-1]["event"] == HubEventType.QUEUE_CHANGED
