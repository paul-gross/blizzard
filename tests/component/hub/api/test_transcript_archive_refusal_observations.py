"""Runner archive refusals observed against the hub's OpenAPI response declarations."""

from pathlib import Path

from fastapi.testclient import TestClient

from blizzard.hub.domain.observability.transcripts import TranscriptCaps
from blizzard.wire import problem
from blizzard.wire.auth import AuthenticationRequiredProblem
from blizzard.wire.transcript_segment import LeaseOwnershipProblem
from tests.support.hub_request_auth import bearer, ingest_chunk
from tests.support.refusal_observation import RefusalObservation, refusal_mismatches
from tests.support.scaffolding import build_hub, seed_runner

_TEMPLATE = "/api/fleet/chunks/{chunk_id}/transcript-segments"


def test_foreign_lease_refusal_matches_openapi_and_preserves_archive(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    owner = bearer(seed_runner(hub, "owner-secret"))
    other = bearer(seed_runner(hub, "other-runner"))
    chunk = ingest_chunk(hub)
    record = {
        "seq": 1,
        "segment_id": "sg_1",
        "chunk_id": chunk,
        "node_id": "nd_build",
        "epoch": 1,
        "spawn_generation": 1,
        "turn_range_start": 0,
        "turn_range_end": 0,
        "final": True,
        "normalizer_version": "v1",
        "harness_version": None,
        "turns": [
            {
                "index": 0,
                "kind": "asst",
                "text": "private",
                "timestamp": None,
                "tool": None,
                "thinking_redacted": False,
                "sidechain": None,
                "truncated": False,
            }
        ],
    }
    pushed = hub.client.post("/api/fleet/transcripts", json={"records": [record]}, headers=owner)
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["applied"] == [1]
    path = f"/api/fleet/chunks/{chunk}/transcript-segments"
    params = {"node_id": "nd_build", "epoch": 1}
    before = hub.client.get(path, params=params, headers=owner)
    assert before.status_code == 200, before.text
    assert before.json()["turns"][0]["text"] == "private"

    response = hub.client.get(path, params=params, headers=other)

    assert response.status_code == 403, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == problem.LEASE_OWNERSHIP
    assert response.json()["instance"] == path
    assert "owner-secret" not in response.text
    observation = RefusalObservation(
        "get", _TEMPLATE, response.status_code, response.headers["content-type"], LeaseOwnershipProblem, response.json()
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []
    after = hub.client.get(path, params=params, headers=owner)
    assert after.status_code == 200
    assert after.json() == before.json()

    anonymous = TestClient(hub.app).get(path, params=params)
    assert anonymous.status_code == 401
    assert anonymous.headers["content-type"] == "application/problem+json"
    assert anonymous.json()["instance"] == path
    assert (
        refusal_mismatches(
            hub.app.openapi(),
            RefusalObservation(
                "get",
                _TEMPLATE,
                401,
                anonymous.headers["content-type"],
                AuthenticationRequiredProblem,
                anonymous.json(),
            ),
        )
        == []
    )
    assert hub.client.get(path, params=params, headers=owner).json() == before.json()


def test_cap_rejected_only_lease_still_refuses_a_foreign_runner(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, transcript_caps=TranscriptCaps(record_max_bytes=200))
    assert hub.app is not None
    owner = bearer(seed_runner(hub, "owner-secret"))
    other = bearer(seed_runner(hub, "other-runner"))
    chunk = ingest_chunk(hub)
    record = {
        "seq": 1,
        "segment_id": "sg_1",
        "chunk_id": chunk,
        "node_id": "nd_build",
        "epoch": 1,
        "spawn_generation": 1,
        "turn_range_start": 0,
        "turn_range_end": 0,
        "final": True,
        "normalizer_version": "v1",
        "harness_version": None,
        "turns": [
            {
                "index": 0,
                "kind": "asst",
                "text": "x" * 500,
                "timestamp": None,
                "tool": None,
                "thinking_redacted": False,
                "sidechain": None,
                "truncated": False,
            }
        ],
    }
    pushed = hub.client.post("/api/fleet/transcripts", json={"records": [record]}, headers=owner)
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["capped"] == [1]
    assert pushed.json()["applied"] == []

    path = f"/api/fleet/chunks/{chunk}/transcript-segments"
    params = {"node_id": "nd_build", "epoch": 1}
    own = hub.client.get(path, params=params, headers=owner)
    assert own.status_code == 200, own.text
    assert own.json()["turns"] == []
    assert own.json()["truncated"] is True

    response = hub.client.get(path, params=params, headers=other)
    assert response.status_code == 403, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == problem.LEASE_OWNERSHIP
    assert response.json()["instance"] == path
    assert "owner-secret" not in response.text
    observation = RefusalObservation(
        "get", _TEMPLATE, response.status_code, response.headers["content-type"], LeaseOwnershipProblem, response.json()
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []
    assert hub.client.get(path, params=params, headers=owner).json() == own.json()

    unrecorded = hub.client.get(path, params={"node_id": "nd_build", "epoch": 2}, headers=other)
    assert unrecorded.status_code == 200, unrecorded.text
    assert unrecorded.json()["turns"] == []
    assert unrecorded.json()["truncated"] is False
