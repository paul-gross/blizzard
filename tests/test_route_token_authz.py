"""Route-token authorization at the wired hub (component tier, issue #84b).

``tests/test_route_claim.py`` covers the token's mint (issue #84a, present-only);
``tests/test_route_auth.py`` covers ``check_route_token`` itself (unit tier). This file
proves the **check** — completions, decisions, and buffered chunk-scoped facts are
rejected under ``route_token_mode=enforce`` when the presented token doesn't match the
chunk's live route, or the declared ``runner_id`` doesn't hold it — release invalidates
the old token, re-key rotates it, ``usage.recorded`` stays ungated, and ``warn`` (the
default) never rejects, only logs.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from blizzard.hub.config import ROUTE_TOKEN_ENFORCE, RUNNER_AUTH_ENFORCE
from tests.support import build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "84"}

# build (worker) -> deliver (hub) — the minimum shape a completion/decision/fact needs
# to reach every check under test.
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


def _ingest(hub) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """Mint the graph, ingest+promote a chunk; return (chunk_id, build node_id)."""
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML})
    assert graph.status_code == 201, graph.text
    build_node_id = next(n["node_id"] for n in graph.json()["nodes"] if n["name"] == "build")
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    return chunk_id, build_node_id


def _claim(hub, chunk_id: str, *, runner_id: str = "r1", headers: dict[str, str] | None = None) -> str:  # type: ignore[no-untyped-def]
    """Claim ``chunk_id`` for ``runner_id``; return the plaintext route token."""
    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": runner_id, "workspace_id": "w1", "environment_ids": ["e"]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["route_token"])


def _completion(node_id: str, *, epoch: int, runner_id: str = "r1", route_token: str | None) -> dict:
    body: dict = {"choice": "pass", "epoch": epoch, "runner_id": runner_id, "from_node_id": node_id, "artifacts": []}
    if route_token is not None:
        body["route_token"] = route_token
    return body


def _submit(hub, chunk_id: str, **kwargs) -> httpx.Response:  # type: ignore[no-untyped-def]
    return hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=_completion(**kwargs))


# --------------------------------------------------------------------------- #
# Completion apply — token match/mismatch, runner mismatch (AC 2, 4)
# --------------------------------------------------------------------------- #


def test_completion_with_the_claims_own_token_is_accepted_under_enforce(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, node_id = _ingest(hub)
    token = _claim(hub, chunk_id)
    report_lease(hub, chunk_id, epoch=1, seq=1, route_token=token)

    resp = _submit(hub, chunk_id, node_id=node_id, epoch=1, route_token=token)

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] != "failure"


def test_completion_with_a_missing_token_is_rejected_under_enforce(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, node_id = _ingest(hub)
    token = _claim(hub, chunk_id)
    report_lease(hub, chunk_id, epoch=1, seq=1, route_token=token)

    resp = _submit(hub, chunk_id, node_id=node_id, epoch=1, route_token=None)

    assert resp.status_code == 200, resp.text  # ApplyResponse — a semantic failure, not an HTTP error
    assert resp.json()["outcome"] == "failure"


def test_completion_with_a_wrong_token_is_rejected_under_enforce(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, node_id = _ingest(hub)
    _claim(hub, chunk_id)
    report_lease(hub, chunk_id, epoch=1, seq=1, route_token="not-the-real-token")

    resp = _submit(hub, chunk_id, node_id=node_id, epoch=1, route_token="not-the-real-token")

    assert resp.json()["outcome"] == "failure"


def test_completion_with_the_wrong_runner_id_is_rejected_under_enforce(tmp_path: Path) -> None:
    """The token is right, but the declared runner_id is not the token's route (AC 4)."""
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, node_id = _ingest(hub)
    token = _claim(hub, chunk_id, runner_id="r1")
    report_lease(hub, chunk_id, epoch=1, seq=1, runner_id="r1", route_token=token)

    resp = _submit(hub, chunk_id, node_id=node_id, epoch=1, runner_id="r2", route_token=token)

    assert resp.json()["outcome"] == "failure"


def test_completion_without_a_token_proceeds_under_warn(tmp_path: Path) -> None:
    """``warn`` is the default: no token at all still applies (no regression from #84a)."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _ingest(hub)
    _claim(hub, chunk_id)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    resp = _submit(hub, chunk_id, node_id=node_id, epoch=1, route_token=None)

    assert resp.json()["outcome"] != "failure"


# --------------------------------------------------------------------------- #
# Fact intake — a fabricated lease.minted cannot advance latest_epoch (AC 3)
# --------------------------------------------------------------------------- #


def test_fabricated_lease_minted_is_rejected_and_cannot_advance_the_fence(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, _node_id = _ingest(hub)
    token = _claim(hub, chunk_id)
    genuine = report_lease(hub, chunk_id, epoch=1, seq=1, route_token=token)
    assert genuine["applied"] == [1]

    fabricated = report_lease(hub, chunk_id, epoch=99, seq=2, route_token="stolen-guess")

    assert fabricated["rejected"] == [2]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["latest_epoch"] == 1


def test_escalation_from_a_non_holder_is_rejected_under_enforce(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, _node_id = _ingest(hub)
    _claim(hub, chunk_id)

    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "escalation.recorded",
                    "payload": {"chunk_id": chunk_id, "epoch": 1, "takeover_command": "cd x && resume"},
                }
            ],
        },
    )

    assert resp.json()["rejected"] == [1]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] != "needs_human"


# --------------------------------------------------------------------------- #
# usage.recorded stays ungated — a deliberate exclusion (issue #84b DO-NOT-GATE)
# --------------------------------------------------------------------------- #


def test_usage_recorded_applies_without_a_token_even_under_enforce(tmp_path: Path) -> None:
    """The regression guard for the deliberate exclusion (facts.py's own no-fence
    rationale, epic #57/#60 cost attribution): unlike lease/escalation/question, a
    usage row from a caller presenting no token at all is still applied."""
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, node_id = _ingest(hub)
    _claim(hub, chunk_id)

    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "usage.recorded",
                    "payload": {
                        "chunk_id": chunk_id,
                        "node_id": node_id,
                        "epoch": 1,
                        "kind": "worker",
                        "model": "claude-opus-4-8",
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_read_tokens": 0,
                        "cache_create_tokens": 0,
                    },
                }
            ],
        },
    )

    assert resp.json()["applied"] == [1]


# --------------------------------------------------------------------------- #
# route.released invalidates the token; a fresh claim's token is accepted (AC 5)
# --------------------------------------------------------------------------- #


def test_release_invalidates_the_old_token_and_the_next_claims_token_is_accepted(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, node_id = _ingest(hub)
    old_token = _claim(hub, chunk_id)
    report_lease(hub, chunk_id, epoch=1, seq=1, route_token=old_token)

    assert hub.client.post(f"/api/chunks/{chunk_id}/detach").status_code == 202

    stale = _submit(hub, chunk_id, node_id=node_id, epoch=1, route_token=old_token)
    assert stale.json()["outcome"] == "failure"

    new_token = _claim(hub, chunk_id)
    assert new_token != old_token
    report_lease(hub, chunk_id, epoch=1, seq=2, route_token=new_token)

    fresh = _submit(hub, chunk_id, node_id=node_id, epoch=1, route_token=new_token)
    assert fresh.json()["outcome"] != "failure"


# --------------------------------------------------------------------------- #
# Re-key — rotates the token; the old plaintext is rejected afterward (AC 6)
# --------------------------------------------------------------------------- #


def test_rekey_rotates_the_token_and_the_old_one_is_rejected_afterward(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, node_id = _ingest(hub)
    old_token = _claim(hub, chunk_id)
    report_lease(hub, chunk_id, epoch=1, seq=1, route_token=old_token)

    rekeyed = hub.client.post(f"/api/fleet/chunks/{chunk_id}/route-token")
    assert rekeyed.status_code == 200, rekeyed.text
    new_token = rekeyed.json()["route_token"]
    assert new_token != old_token
    assert rekeyed.json()["chunk_id"] == chunk_id

    stale = _submit(hub, chunk_id, node_id=node_id, epoch=1, route_token=old_token)
    assert stale.json()["outcome"] == "failure"

    fresh = _submit(hub, chunk_id, node_id=node_id, epoch=1, route_token=new_token)
    assert fresh.json()["outcome"] != "failure"


def test_rekey_on_a_chunk_with_no_live_route_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _node_id = _ingest(hub)  # ready, never claimed — no live route

    resp = hub.client.post(f"/api/fleet/chunks/{chunk_id}/route-token")

    assert resp.status_code == 404


def test_rekey_is_confined_to_the_live_routes_own_runner(tmp_path: Path) -> None:
    """``assert_owns`` against the live route's runner (runner_auth_mode=enforce) —
    a different runner's own valid bearer token cannot re-key someone else's route."""
    warn_hub = build_hub(tmp_path)
    for runner_id in ("r1", "r2"):
        assert (
            warn_hub.client.post("/api/fleet/runners", json={"runner_id": runner_id, "workspace_id": "w1"}).status_code
            == 201
        )
    token_r1 = warn_hub.client.post("/api/runners/r1/enrollments").json()["token"]
    token_r2 = warn_hub.client.post("/api/runners/r2/enrollments").json()["token"]

    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    chunk_id, _node_id = _ingest(hub)
    _claim(hub, chunk_id, runner_id="r1", headers={"Authorization": f"Bearer {token_r1}"})

    denied = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/route-token", headers={"Authorization": f"Bearer {token_r2}"}
    )
    assert denied.status_code == 403

    allowed = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/route-token", headers={"Authorization": f"Bearer {token_r1}"}
    )
    assert allowed.status_code == 200, allowed.text


# --------------------------------------------------------------------------- #
# A runner-config gate decision is gated the same way as a completion
# --------------------------------------------------------------------------- #


def test_decision_submission_with_a_wrong_token_is_rejected_under_enforce(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, node_id = _ingest(hub)
    _claim(hub, chunk_id)

    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/decisions",
        json={
            "from_node_id": node_id,
            "epoch": 1,
            "runner_id": "r1",
            "artifacts": [],
            "route_token": "not-the-real-token",
        },
    )

    assert resp.json()["outcome"] == "failure"


def test_decision_submission_with_the_right_token_is_accepted_under_enforce(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, node_id = _ingest(hub)
    token = _claim(hub, chunk_id)

    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/decisions",
        json={"from_node_id": node_id, "epoch": 1, "runner_id": "r1", "artifacts": [], "route_token": token},
    )

    assert resp.json()["outcome"] == "parked_at_gate", resp.text
