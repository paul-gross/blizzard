"""Observe distinct finding and queue refusals at their runtime HTTP boundaries."""

from datetime import UTC, datetime
from pathlib import Path

from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.wire.chunk import ChunkNotFoundProblem, FindingNotInChunkProblem, NoAnsweredProposalProblem
from blizzard.wire.project import UnknownProjectProblem
from tests.support.refusal_observation import RefusalObservation, refusal_mismatches, unobserved_variants
from tests.support.scaffolding import build_hub, default_project, hub_store_connections


def test_finding_read_distinguishes_missing_chunk_proposal_and_membership(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    created = hub.client.post("/api/chunks", json={"tokens": ["default:1"]})
    assert created.status_code == 201, created.text
    plain = created.json()["chunk_id"]
    GardenProposalStore(hub_store_connections(hub.engine)).create(
        "gprop_observation",
        project_id=default_project(hub.engine),
        origin=GardenProposalOrigin.ROUTINE_RUN,
        routine_name="nightly",
        class_="fix",
        title="title",
        body="body",
        findings=[],
        at=datetime(2026, 9, 4, tzinfo=UTC),
    )
    accepted = hub.client.post("/api/garden-proposals/gprop_observation/accept", json={})
    assert accepted.status_code == 200, accepted.text
    answered = accepted.json()["chunk_id"]
    template = "/api/fleet/chunks/{chunk_id}/findings/{finding_id}"
    spec = hub.app.openapi()
    for chunk, model, detail in (
        ("ch_missing", ChunkNotFoundProblem, "unknown chunk"),
        (plain, NoAnsweredProposalProblem, "no accepted, minted garden proposal"),
        (answered, FindingNotInChunkProblem, "not among the findings"),
    ):
        response = hub.client.get(f"/api/fleet/chunks/{chunk}/findings/fin_missing")
        assert response.status_code == 404, response.text
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["instance"] == response.request.url.path
        assert detail in response.json()["detail"]
        assert (
            refusal_mismatches(
                spec, RefusalObservation("get", template, 404, response.headers["content-type"], model, response.json())
            )
            == []
        )
    assert unobserved_variants(
        spec,
        "get",
        template,
        404,
        "application/problem+json",
        {ChunkNotFoundProblem, NoAnsweredProposalProblem, FindingNotInChunkProblem},
    ) == {"TenantNotFoundProblem"}


def test_queue_peek_unknown_project_is_a_declared_validation_refusal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    path = "/api/fleet/queue/peek"
    response = hub.client.get(path, params={"project": "missing-project"})
    assert response.status_code == 422, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["instance"] == path
    observation = RefusalObservation(
        "get", path, 422, response.headers["content-type"], UnknownProjectProblem, response.json()
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []
