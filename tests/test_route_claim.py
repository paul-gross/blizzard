"""Route claim — exactly-one-wins and the first-node envelope (component tier)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub

pytestmark = pytest.mark.component

_POINTER = {"provider": "github", "url": "http://forge.local/repos/acme/widget/issues/7"}


def _ingest(hub) -> str:  # type: ignore[no-untyped-def]
    return hub.client.post("/api/chunks", json={"pointers": [_POINTER]}).json()["chunk_id"]


def _claim_body(chunk_id: str, runner: str = "r1") -> dict:
    return {"chunk_id": chunk_id, "runner_id": runner, "workspace_id": "w1", "environment_ids": ["env-a", "env-b"]}


def test_winning_claim_carries_the_first_node_envelope(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _ingest(hub)

    resp = hub.client.post("/api/routes", json=_claim_body(chunk_id))
    assert resp.status_code == 201
    body = resp.json()
    assert body["environment_ids"] == ["env-a", "env-b"]
    env = body["envelope"]
    assert env["chunk_id"] == chunk_id
    assert env["epoch"] == 1
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

    # The lost-apply recovery read returns the same current-node envelope (D-090).
    reread = hub.client.get(f"/api/chunks/{chunk_id}/envelope").json()
    assert reread["node"]["node_id"] == claimed["node"]["node_id"]
    assert reread["epoch"] == claimed["epoch"]
