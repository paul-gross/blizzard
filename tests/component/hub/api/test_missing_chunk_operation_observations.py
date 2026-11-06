"""Observe missing-chunk refusals on individual fleet operations."""

from pathlib import Path

import pytest

from blizzard.wire.chunk import ChunkNotFoundProblem
from tests.support.refusal_observation import RefusalObservation, refusal_mismatches
from tests.support.scaffolding import build_hub


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("get", "findings", None),
        ("post", "release", {"runner_id": "r1", "epoch": 1, "reason": "throttled"}),
        ("post", "hub-advance", {}),
    ],
)
def test_missing_chunk_refusal_matches_the_operation(
    tmp_path: Path, method: str, suffix: str, body: dict[str, object] | None
) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    template = f"/api/fleet/chunks/{{chunk_id}}/{suffix}"
    url = template.format(chunk_id="ch_missing")
    before = hub.client.get("/api/activity").json()

    response = hub.client.request(method, url, json=body)

    assert response.status_code == 404, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["instance"] == url
    assert response.json()["detail"] == "unknown chunk ch_missing"
    observation = RefusalObservation(
        method, template, 404, response.headers["content-type"], ChunkNotFoundProblem, response.json()
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []
    assert hub.client.get("/api/activity").json() == before
