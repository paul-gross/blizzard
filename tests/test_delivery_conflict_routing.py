"""The SHIPPED ``advanced-development-workflow`` graph's ``deliver`` node routes each answer
its ``land`` action gives against the in-memory GitHub double — component tier.

Mints the real, packaged graph, seeding the chunk directly at ``deliver``'s minted node
id via a direct transition-fact insert.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.hub.documents import YAML_CODEC
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.escalations import IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.graph.model import DEFAULT_BOUNCE_CAP
from blizzard.hub.forge.landing import Mergeability
from blizzard.hub.graphs import PACKAGED
from tests.github_double import RED, GitHubDouble, change, forge_holding
from tests.support import (
    DEFAULT_FIXTURE_REPOSITORIES,
    HubHarness,
    build_hub,
    chunk_facts_of,
    make_ready,
    pointer_token,
    report_lease,
)

pytestmark = pytest.mark.component

_COMMIT = "c" * 40


def _forge(*repos: str, **state: object) -> GitHubDouble:
    return forge_holding(*(repos or ("acme/widget",)), branch="feat/thing", commit=_COMMIT, **state)


def _writable_movement(hub: HubHarness) -> IWriteChunkMovementRepository:
    return cast(IWriteChunkMovementRepository, hub.services.chunks.movement)


def _writable_escalations(hub: HubHarness) -> IWriteChunkEscalationsRepository:
    return cast(IWriteChunkEscalationsRepository, hub.services.chunks.escalations)


def _mint_and_claim(hub: HubHarness) -> tuple[str, dict[str, str]]:
    """Mint the packaged adv-dwf graph, ingest a chunk (which pins to the hub's own
    packaged **default** graph — ingest names no graph), then repin it onto adv-dwf via
    ``PATCH /chunks/{id}`` (legal while the chunk is still ``not_ready``) before claiming
    a route, so every node id resolved off the mint response is the one the claimed
    chunk's pin actually recognizes."""
    definition_yaml = PACKAGED.named("advanced-development-workflow").inlined_yaml(YAML_CODEC)
    minted = hub.client.post("/api/graphs", json={"definition_yaml": definition_yaml})
    assert minted.status_code == 201, minted.text
    graph_id = minted.json()["graph_id"]
    nodes = {n["name"]: n["node_id"] for n in minted.json()["nodes"]}
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "1"})]}
    ).json()["chunk_id"]
    repin = hub.client.patch(f"/api/chunks/{chunk_id}", json={"graph_id": graph_id})
    assert repin.status_code == 202, repin.text
    make_ready(hub, chunk_id)
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e1"]},
    )
    return chunk_id, nodes


def _seed_at_deliver_with_an_unlanded_commit(
    hub: HubHarness, chunk_id: str, nodes: dict[str, str], *, repos: tuple[str, ...] = ("acme/widget",)
) -> None:
    """Place the chunk's current node directly at ``deliver`` (standing in for the six
    node-steps a real chunk would take to arrive here — same technique
    ``tests/test_delivery_incomplete_routing.py`` uses for ``retrospective``), carrying
    one ``git_commit`` artifact for a repo with no ``merged/<repo>`` marker, so this
    reads as a genuine, unlanded delivery attempt."""
    commit_artifacts = [
        StoredArtifact(
            kind=ArtifactKind.GIT_COMMIT,
            name=repo.split("/")[1],
            data=f"feat/thing:{_COMMIT}",
            repo=repo,
            forge=None,
            artifact_id=Id.mint(IdPrefix.ARTIFACT, hub.clock).value,
            chunk_id=chunk_id,
            node_id=nodes["build"],
            node_name="build",
            epoch=1,
        )
        for repo in repos
    ]
    _writable_movement(hub).record_transition(
        transition_id="tr_seed_deliver",
        chunk_id=chunk_id,
        from_node_id=None,
        to_node_id=nodes["deliver"],
        choice_name=None,
        epoch=1,
        runner_id="r1",
        at=hub.clock.now(),
        artifacts=commit_artifacts,
        admission=EpochAdmission.AT_OR_ABOVE,
    )


def test_a_dirty_conflict_routes_to_resolve_and_records_a_bounce(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, forges=_forge(mergeability=Mergeability.CONFLICTING).forges)
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver_with_an_unlanded_commit(hub, chunk_id, nodes)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    advance = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    body = advance.json()
    assert body["ran"] is True
    assert body["outcome_choice"] == "conflict"

    # The accepted transition's target node is `resolve` — the named assertion a
    # mutation proof (deleting the choice from graph.yaml) must break.
    assert body["to_node_name"] == "resolve"

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["current_node_id"] == nodes["resolve"]
    assert detail["current_node_name"] == "resolve"

    assert len(detail["bounces"]) == 1
    assert detail["bounces"][0]["cause"] == "conflict"
    bounce_assets = [a for a in detail["artifacts"] if a["name"] == "bounce-envelope"]
    assert len(bounce_assets) == 1, detail["artifacts"]
    assert detail["landed"] is False

    # The negative that is the whole point of this test: no unroutable-outcome
    # artifact, no unroutable-outcome event.
    unroutable_artifacts = [a for a in detail["artifacts"] if a["name"] == "hub-unroutable-outcome"]
    assert unroutable_artifacts == []
    unroutable_events = [
        e for e in hub.services.chunks.events.list_events(chunk_id=chunk_id) if e.kind == "hub-node-unroutable-outcome"
    ]
    assert unroutable_events == []


def test_a_dirty_conflict_escalates_once_the_bounce_cap_is_crossed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, forges=_forge(mergeability=Mergeability.CONFLICTING).forges)
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver_with_an_unlanded_commit(hub, chunk_id, nodes)

    # `deliver` falls back to `DEFAULT_BOUNCE_CAP` (5) — pre-seed that many prior bounces
    # (idempotent per `(chunk_id, epoch)`) so this run's kick-back crosses it.
    for epoch in range(1, DEFAULT_BOUNCE_CAP + 1):
        _writable_escalations(hub).record_bounce(
            chunk_id, epoch=epoch, cause="conflict", envelope="{}", at=hub.clock.now()
        )
    report_lease(hub, chunk_id, epoch=DEFAULT_BOUNCE_CAP + 1, seq=1)

    advance = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    body = advance.json()
    assert body["ran"] is True

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "needs_human"
    assert len(detail["bounces"]) == DEFAULT_BOUNCE_CAP + 1
    assert detail["escalation"]["cause"] == "bounce-cap"
    assert detail["escalation"]["detail"] == (
        f"bounce cap ({DEFAULT_BOUNCE_CAP}) crossed after {DEFAULT_BOUNCE_CAP + 1} bounces"
    )
    # The escalation holds the route: the runner's tenure outlives it, so a requeue resumes in place.
    assert chunk_facts_of(hub, chunk_id).routes.newest is not None


def _envelope(hub: HubHarness, chunk_id: str) -> dict:
    (bounce,) = hub.client.get(f"/api/chunks/{chunk_id}").json()["bounces"]
    return json.loads(bounce["envelope"])


def test_a_red_check_envelope_names_the_unlanded_repo_its_pr_and_the_land_action(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, forges=_forge(checks={_COMMIT: RED}).forges)
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver_with_an_unlanded_commit(hub, chunk_id, nodes)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    body = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance").json()
    assert (body["outcome_choice"], body["to_node_name"]) == ("failure", "resolve")

    envelope = _envelope(hub, chunk_id)
    assert [u["repo"] for u in envelope["unlanded"]] == ["acme/widget"]
    assert envelope["unlanded"][0]["branch"] == "feat/thing"
    assert envelope["unlanded"][0]["commit"] == _COMMIT
    assert envelope["unlanded"][0]["pr"] == {"number": 1, "url": "https://github.example/acme/widget/pull/1"}
    assert envelope["step"] is None
    assert envelope["action"] == {"name": "land", "choice": "failure", "log_artifact": "hub-log.land"}
    assert envelope["detail"] == "PR #1 in acme/widget did not land: `land` answered failure"
    artifact_names = {a["name"] for a in hub.client.get(f"/api/chunks/{chunk_id}").json()["artifacts"]}
    assert "hub-log.land" in artifact_names


def test_an_envelope_lists_only_the_unlanded_repo_with_its_pr(tmp_path: Path) -> None:
    pr = replace(
        change("acme/widget", 945, branch="feat/thing", sha=_COMMIT, base="main"),
        web_url="https://x/acme/widget/pull/945",
    )
    double = _forge("acme/done", "acme/widget", mergeability=Mergeability.CONFLICTING)
    double.repos["acme/widget"].changes[945] = pr
    repositories = [replace(DEFAULT_FIXTURE_REPOSITORIES[0], repo=name) for name in ("widget", "done")]
    hub = build_hub(tmp_path, forges=double.forges, repositories=repositories)
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver_with_an_unlanded_commit(hub, chunk_id, nodes, repos=("acme/done", "acme/widget"))
    _record(hub, chunk_id, nodes, "merged/acme/done", "d" * 40)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")

    envelope = _envelope(hub, chunk_id)
    assert [u["repo"] for u in envelope["unlanded"]] == ["acme/widget"]
    assert envelope["unlanded"][0]["pr"] == {"number": 945, "url": "https://x/acme/widget/pull/945"}
    assert envelope["detail"].startswith("PR #945 in acme/widget did not land: `land` answered ")
    assert [repo for repo, _ in double.named("open_change")] == []


def test_a_conflict_answered_by_the_action_names_no_step_in_its_envelope(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, forges=_forge(mergeability=Mergeability.CONFLICTING).forges)
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver_with_an_unlanded_commit(hub, chunk_id, nodes)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")

    envelope = _envelope(hub, chunk_id)
    assert envelope["step"] is None
    assert envelope["action"]["choice"] == "conflict"
    assert "conflict" in envelope["detail"] and "acme/widget" in envelope["detail"]
    assert "exited" not in envelope["detail"]


def test_a_blank_landing_marker_leaves_its_repo_unlanded_in_the_envelope(tmp_path: Path) -> None:
    repositories = [replace(DEFAULT_FIXTURE_REPOSITORIES[0], repo=name) for name in ("widget", "gadget")]
    double = _forge("acme/widget", "acme/gadget", mergeability=Mergeability.CONFLICTING)
    hub = build_hub(tmp_path, forges=double.forges, repositories=repositories)
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver_with_an_unlanded_commit(hub, chunk_id, nodes, repos=("acme/widget", "acme/gadget"))
    _record(hub, chunk_id, nodes, "merged/acme/widget", "  ")
    report_lease(hub, chunk_id, epoch=1, seq=1)
    hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")

    assert sorted(u["repo"] for u in _envelope(hub, chunk_id)["unlanded"]) == ["acme/gadget", "acme/widget"]


def _record(hub: HubHarness, chunk_id: str, nodes: dict[str, str], name: str, content: str) -> None:
    cast(IWriteChunkArtifactsRepository, hub.services.chunks.artifacts).record_hub_artifact(
        chunk_id,
        node_id=nodes["deliver"],
        node_name="deliver",
        epoch=1,
        admission=EpochAdmission.AT_OR_ABOVE,
        name=name,
        content=content,
        at=hub.clock.now(),
    )
