"""Route claim — exactly-one-wins, paused-denial, and the first-node envelope (component tier)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "7"}


def _ingest(hub, ref: str = "7") -> str:  # type: ignore[no-untyped-def]
    pointer = {"source": "default", "ref": ref}
    return hub.client.post("/api/chunks", json={"tokens": [pointer_token(pointer)]}).json()["chunk_id"]


def _claim_body(chunk_id: str, runner: str = "r1") -> dict:
    return {"chunk_id": chunk_id, "runner_id": runner, "workspace_id": "w1", "environment_ids": ["env-a", "env-b"]}


def _register(hub, runner_id: str = "r1", workspace_id: str = "w1") -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/runners", json={"runner_id": runner_id, "workspace_id": workspace_id})
    assert resp.status_code == 201, resp.text


def test_winning_claim_carries_the_first_node_envelope(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _ingest(hub)

    resp = hub.client.post("/api/routes", json=_claim_body(chunk_id))
    assert resp.status_code == 201
    body = resp.json()
    assert body["environment_ids"] == ["env-a", "env-b"]
    env = body["envelope"]
    assert env["chunk_id"] == chunk_id
    # The claim does not mint the lease: the runner reports its epoch via
    # POST /events, so the claim envelope carries the current epoch (0, no lease yet).
    assert env["epoch"] == 0
    assert env["node"]["node_name"] == "build"
    assert env["node"]["executor"] == "runner"
    # The envelope carries the pre-prompt, the authored judgement prose (the runner
    # appends the elicitation tail from the choice set), the choice set, and the
    # chunk's PM pointers.
    assert env["prompt"]
    assert env["judgement_prompt"]
    assert "<Choice>" not in env["judgement_prompt"]  # the tail is the runner's to render
    assert {c["name"] for c in env["node"]["choices"]} == {"pass", "fail"}
    assert env["pm_pointers"] == [_POINTER]


def test_second_claim_loses_with_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _ingest(hub)

    assert hub.client.post("/api/routes", json=_claim_body(chunk_id, "r1")).status_code == 201
    loser = hub.client.post("/api/routes", json=_claim_body(chunk_id, "r2"))
    assert loser.status_code == 409
    assert loser.json()["held_by_runner_id"] == "r1"


def test_claim_on_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/routes", json=_claim_body("ch_missing")).status_code == 404


def test_envelope_reread_is_idempotent(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _ingest(hub)
    claimed = hub.client.post("/api/routes", json=_claim_body(chunk_id)).json()["envelope"]

    # The lost-apply recovery read returns the same current-node envelope.
    reread = hub.client.get(f"/api/chunks/{chunk_id}/envelope").json()
    assert reread["node"]["node_id"] == claimed["node"]["node_id"]
    assert reread["epoch"] == claimed["epoch"]


# --------------------------------------------------------------------------- #
# The hub denies a claim from a registry-paused runner outright (issue #44)
# --------------------------------------------------------------------------- #
#
# A distinct outcome from the 409 race loss above: this claim never enters the
# exactly-once race at all — the hub refuses it because its own registry already
# marks the claiming runner paused, independent of whether the runner has read the
# flag back on its own pull yet.


def test_claim_denied_while_hub_paused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _ingest(hub)
    _register(hub, "r1")
    assert hub.client.post("/api/runners/r1/pause", json={"by": "operator"}).status_code == 200

    resp = hub.client.post("/api/routes", json=_claim_body(chunk_id, "r1"))

    assert resp.status_code == 403
    body = resp.json()
    assert body["chunk_id"] == chunk_id
    assert body["runner_id"] == "r1"
    # Distinguishable from a 409 conflict: no other runner holds this chunk, it was
    # simply never allowed to enter the race — it stays claimable.
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] != "running"


def test_claim_allowed_after_resume(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _ingest(hub)
    _register(hub, "r1")
    hub.client.post("/api/runners/r1/pause", json={"by": "operator"})
    assert hub.client.post("/api/routes", json=_claim_body(chunk_id, "r1")).status_code == 403

    assert hub.client.post("/api/runners/r1/resume", json={"by": "operator"}).status_code == 200
    resp = hub.client.post("/api/routes", json=_claim_body(chunk_id, "r1"))

    assert resp.status_code == 201
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"


def test_claim_denied_the_instant_the_pause_lands_mid_tick(tmp_path: Path) -> None:
    """The motivating race: a pause landing at the hub *between* a runner's last pull and
    its claim POST — the window this issue closes. There is no PULL/tick machinery at
    this tier, so the race is expressed the way the hub actually sees it: the pause fact
    is already durable by the time ``POST /routes`` arrives, whatever the claiming
    runner's own (possibly stale) local copy of the brake says."""
    hub = build_hub(tmp_path)
    chunk_id = _ingest(hub)
    _register(hub, "r1")
    # Simulate the operator's pause landing after the runner's last pull would have
    # mirrored "not paused" but before its in-flight claim reaches the hub.
    hub.client.post("/api/runners/r1/pause", json={"by": "operator"})

    resp = hub.client.post("/api/routes", json=_claim_body(chunk_id, "r1"))

    assert resp.status_code == 403  # the hub is the arbiter — it catches what the runner missed
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["route"] is None


def test_claim_allowed_while_only_locally_paused(tmp_path: Path) -> None:
    """Local pause (issue #43) is the runner's own restraint — the hub never denies on it."""
    hub = build_hub(tmp_path)
    chunk_id = _ingest(hub)
    _register(hub, "r1")
    reported = hub.client.post(
        "/api/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 1, "kind": "runner.locally_paused", "payload": {"runner_id": "r1", "by": "alice"}}],
        },
    )
    assert reported.status_code == 200
    view = hub.client.get("/api/runners/r1").json()
    assert view["locally_paused"] is True
    assert view["hub_paused"] is False

    resp = hub.client.post("/api/routes", json=_claim_body(chunk_id, "r1"))

    assert resp.status_code == 201


def test_claim_from_an_unregistered_runner_is_not_denied(tmp_path: Path) -> None:
    """A runner the registry has never heard from cannot be paused there — `set_paused`
    requires a known runner — so an unregistered claimant is not refused on this brake."""
    hub = build_hub(tmp_path)
    chunk_id = _ingest(hub)

    resp = hub.client.post("/api/routes", json=_claim_body(chunk_id, "r-unregistered"))

    assert resp.status_code == 201


def test_in_flight_submission_unaffected_while_hub_paused(tmp_path: Path) -> None:
    """Pause stops new claims; it does not strand work already held."""
    hub = build_hub(tmp_path)
    chunk_id = _ingest(hub)
    _register(hub, "r1")
    claimed = hub.client.post("/api/routes", json=_claim_body(chunk_id, "r1")).json()
    node_id = claimed["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)

    hub.client.post("/api/runners/r1/pause", json={"by": "operator"})

    resp = hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": node_id, "artifacts": []},
    )

    assert resp.status_code == 200
    assert resp.json()["outcome"] != "failure"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["current_node_name"] == "review"
