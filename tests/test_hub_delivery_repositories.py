"""A hub step resolves its chunk's commits to repository records before any command runs (component tier)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
import sqlalchemy as sa

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.hub_event_types import HubEventType
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.hub.delivery.command_runner import CommandResult
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.graphs import PACKAGED
from blizzard.hub.store import schema
from tests.support import (
    FIXTURE_FORGE_SECRET,
    FakeHubCommandRunner,
    FakeHubWorkdir,
    HubHarness,
    build_hub,
    create_repositories,
    default_tenant,
    emitted_events,
    fixture_repository,
    link_project,
    pointer_token,
    report_lease,
    seed_project,
)

pytestmark = pytest.mark.component

_FORGE = "http://forge.example"
_ENV_NAMES = {
    "BZ_HUB_BASE_BRANCH",
    "BZ_HUB_GIT_COMMITS",
    "BZ_FORGE_URL",
    "BZ_FORGE_TOKEN",
    "BZ_FORGE_OWNER",
}


def _mint_and_claim(hub: HubHarness, ref: str = "1", *, project: str | None = None) -> tuple[str, dict[str, str]]:
    minted = hub.client.post(
        "/api/graphs", json={"definition_yaml": PACKAGED.named("advanced-development-workflow").inlined_yaml}
    )
    assert minted.status_code == 201, minted.text
    nodes = {n["name"]: n["node_id"] for n in minted.json()["nodes"]}
    body: dict[str, object] = {"tokens": [pointer_token({"source": "default", "ref": ref})]}
    if project is not None:
        body["project"] = project
    ingested = hub.client.post("/api/chunks", json=body)
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    repin = hub.client.patch(f"/api/chunks/{chunk_id}", json={"graph_id": minted.json()["graph_id"]})
    assert repin.status_code == 202, repin.text
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e1"]},
    )
    return chunk_id, nodes


def _seed_at_deliver(
    hub: HubHarness, chunk_id: str, nodes: dict[str, str], repos: list[tuple[str, str | None]]
) -> None:
    """Place the chunk at ``deliver`` carrying one ``git_commit`` per ``(repo, origin)``."""
    rows = [
        StoredArtifact(
            kind=ArtifactKind.GIT_COMMIT,
            name="w",
            data=f"feat/x:{'a' * 40}",
            repo=repo,
            forge=origin,
            artifact_id=Id.mint(IdPrefix.ARTIFACT, hub.clock).value,
            chunk_id=chunk_id,
            node_id=nodes["build"],
            node_name="build",
            epoch=1,
        )
        for repo, origin in repos
    ]
    cast(IWriteChunkMovementRepository, hub.services.chunks.movement).record_transition(
        transition_id=f"tr_seed_{chunk_id}",
        chunk_id=chunk_id,
        from_node_id=None,
        to_node_id=nodes["deliver"],
        choice_name=None,
        epoch=1,
        runner_id="r1",
        at=hub.clock.now(),
        artifacts=rows,
        admission=__import__(
            "blizzard.hub.domain.chunk.ports.fence", fromlist=["EpochAdmission"]
        ).EpochAdmission.AT_OR_ABOVE,
    )
    report_lease(hub, chunk_id, epoch=1, seq=1)


def _visit(hub: HubHarness, chunk_id: str, nodes: dict[str, str], node_name: str = "deliver"):  # type: ignore[no-untyped-def]
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None
    node = graph.node_by_id(nodes[node_name])
    assert node is not None
    result = hub.services.hub_node.run(chunk, graph, node, epoch=1)
    assert result is not None
    return result


def _event_kinds(hub: HubHarness) -> list[str]:
    logged = [json.loads(e["data"]) for e in emitted_events(hub) if e["event"] == HubEventType.EVENT_LOGGED]
    return [e["kind"] for e in logged]


def _hub(tmp_path: Path) -> tuple[HubHarness, FakeHubCommandRunner]:
    # `deliver` authors no `success`, so a silent run would route `failure`; print the landing outcome.
    runner = FakeHubCommandRunner(default=CommandResult(exit_code=0, stdout="landed\n", stderr=""))
    return build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir(), repositories=()), runner


def test_a_resolved_visit_fills_every_contract_variable_from_the_repository(tmp_path: Path) -> None:
    hub, runner = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme", base_branch="trunk")])
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None)])

    _visit(hub, chunk_id, nodes)

    env = runner.calls[0][2]
    assert env.keys() >= _ENV_NAMES
    assert env["BZ_HUB_BASE_BRANCH"] == "trunk"
    assert env["BZ_FORGE_URL"] == _FORGE
    assert env["BZ_FORGE_OWNER"] == "acme"
    assert env["BZ_FORGE_TOKEN"] == "fixture-token"
    assert [c["repo"] for c in json.loads(env["BZ_HUB_GIT_COMMITS"])] == ["acme/widget"]


def test_a_secret_replaced_between_visits_reaches_the_next_visit(tmp_path: Path) -> None:
    hub, runner = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme")])
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None)])

    _visit(hub, chunk_id, nodes)
    replaced = hub.client.put(f"/api/secrets/{FIXTURE_FORGE_SECRET}/value", json={"value": "rotated-token"})
    assert replaced.status_code == 200, replaced.text
    # The first visit landed the chunk, so the next visit is a second chunk at `deliver`.
    next_chunk_id, next_nodes = _mint_and_claim(hub, "2")
    _seed_at_deliver(hub, next_chunk_id, next_nodes, [("widget", None)])
    _visit(hub, next_chunk_id, next_nodes)

    assert [call[2]["BZ_FORGE_TOKEN"] for call in runner.calls] == ["fixture-token", "rotated-token"]


def test_an_unresolved_repository_runs_no_command_routes_failure_and_is_recorded(tmp_path: Path) -> None:
    hub, runner = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme")])
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("gadget", None)])

    result = _visit(hub, chunk_id, nodes)

    assert result.outcome_choice == "failure"
    assert runner.calls == []
    assert "repository-unresolved" in _event_kinds(hub)
    names = [a["name"] for a in hub.client.get(f"/api/chunks/{chunk_id}").json()["artifacts"]]
    assert "hub-log.repository-unresolved" in names


def test_a_repository_retired_after_the_chunk_minted_still_resolves_but_a_chunk_minted_after_does_not(
    tmp_path: Path,
) -> None:
    hub, runner = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme")])
    earlier, earlier_nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, earlier, earlier_nodes, [("widget", None)])
    hub.clock.advance(timedelta(hours=1))
    assert hub.client.post("/api/repositories/widget/retire").status_code == 200
    hub.clock.advance(timedelta(hours=1))
    earlier_chunk = hub.services.chunks.record.get(earlier)
    assert earlier_chunk is not None
    later = hub.client.post("/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "2"})]}).json()[
        "chunk_id"
    ]
    hub.client.patch(f"/api/chunks/{later}", json={"graph_id": earlier_chunk.graph_id})
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": later, "runner_id": "r1", "workspace_id": "w2", "environment_ids": ["e2"]},
    )
    _seed_at_deliver(hub, later, earlier_nodes, [("widget", None)])

    _visit(hub, earlier, earlier_nodes)
    assert len(runner.calls) == 1
    assert _visit(hub, later, earlier_nodes).outcome_choice == "failure"
    assert len(runner.calls) == 1
    assert "repository-unresolved" in _event_kinds(hub)


def test_repositories_that_disagree_run_no_command_and_are_recorded(tmp_path: Path) -> None:
    hub, runner = _hub(tmp_path)
    create_repositories(
        hub.client,
        [
            fixture_repository("widget", _FORGE, owner="acme"),
            fixture_repository("gadget", _FORGE, owner="acme", base_branch="develop"),
        ],
    )
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None), ("gadget", None)])

    result = _visit(hub, chunk_id, nodes)

    assert result.outcome_choice == "failure"
    assert runner.calls == []
    assert "repositories-disagree" in _event_kinds(hub)


def test_a_chunk_with_no_commits_runs_with_no_forge_variables(tmp_path: Path) -> None:
    hub, runner = _hub(tmp_path)
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [])

    _visit(hub, chunk_id, nodes)

    env = runner.calls[0][2]
    assert json.loads(env["BZ_HUB_GIT_COMMITS"]) == []
    assert not {"BZ_HUB_BASE_BRANCH", "BZ_FORGE_URL", "BZ_FORGE_TOKEN", "BZ_FORGE_OWNER"} & env.keys()


def test_a_name_ambiguous_across_the_tenant_lands_in_the_record_its_project_links(tmp_path: Path) -> None:
    hub, runner = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme")], project=None)
    fork = fixture_repository("widget", _FORGE, owner="other") | {"name": "widget-fork"}
    create_repositories(hub.client, [fork])
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None)])

    _visit(hub, chunk_id, nodes)

    env = runner.calls[0][2]
    assert env["BZ_FORGE_OWNER"] == "other"
    assert [c["repo"] for c in json.loads(env["BZ_HUB_GIT_COMMITS"])] == ["other/widget"]


def test_a_repository_its_project_does_not_link_runs_no_command_and_is_recorded_as_not_linked(tmp_path: Path) -> None:
    hub, runner = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme")], project=None)
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None)])

    result = _visit(hub, chunk_id, nodes)

    assert runner.calls == []
    assert result.outcome_choice == "failure"
    event_log = schema.event_log
    with hub.engine.connect() as conn:
        recorded = conn.execute(
            sa.select(event_log.c.severity, event_log.c.message).where(
                event_log.c.tenant_id == default_tenant(hub.engine), event_log.c.kind == "repository-not-linked"
            )
        ).one()
    assert recorded.severity == "critical"
    assert "repository record widget" in recorded.message
    assert "project default" in recorded.message
    assert "repository-unresolved" not in _event_kinds(hub)
    names = [a["name"] for a in hub.client.get(f"/api/chunks/{chunk_id}").json()["artifacts"]]
    assert "hub-log.repository-not-linked" in names


def test_a_hub_node_that_lands_nothing_still_refuses_a_repository_its_project_does_not_link(tmp_path: Path) -> None:
    hub, runner = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme")], project=None)
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None)])

    result = _visit(hub, chunk_id, nodes, "record-findings")

    assert runner.calls == []
    assert result.outcome_choice == "failure"
    assert "repository-not-linked" in _event_kinds(hub)


def test_a_landing_names_the_record_its_visit_started_against_though_unlinked_mid_step(tmp_path: Path) -> None:
    hub, runner = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme")])
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None)])

    def land_after_unlinking(_command: str) -> None:
        unlinked = hub.client.post("/api/projects/default/repository-links/widget/retire")
        assert unlinked.status_code == 200, unlinked.text
        hub.services.hub_node.record_marker(
            chunk_id, node_id=nodes["deliver"], node_name="deliver", epoch=1, name="merged/acme/widget", content="c1"
        )
        hub.services.hub_node.record_marker(
            chunk_id, node_id=nodes["deliver"], node_name="deliver", epoch=1, name="merged/acme/gadget", content="c2"
        )

    runner.before_run = land_after_unlinking
    _visit(hub, chunk_id, nodes)

    landed = schema.delivery_repo_landed
    with hub.engine.connect() as conn:
        rows = dict(
            conn.execute(
                sa.select(landed.c.repo, landed.c.repository_name).where(
                    landed.c.tenant_id == default_tenant(hub.engine), landed.c.chunk_id == chunk_id
                )
            )
            .tuples()
            .all()
        )
    assert rows == {"acme/widget": "widget", "acme/gadget": None}


def test_the_env_names_the_chunks_project_by_its_id_and_its_slug_as_the_visit_starts(tmp_path: Path) -> None:
    hub, runner = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme")])
    project = hub.client.get("/api/projects/default").json()
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None)])
    _visit(hub, chunk_id, nodes)

    renamed = hub.client.patch("/api/projects/default", json={"slug": "widgets"})
    assert renamed.status_code == 200, renamed.text
    next_chunk_id, next_nodes = _mint_and_claim(hub, "2")
    _seed_at_deliver(hub, next_chunk_id, next_nodes, [("widget", None)])
    _visit(hub, next_chunk_id, next_nodes)

    assert [(call[2]["BZ_HUB_PROJECT_ID"], call[2]["BZ_HUB_PROJECT"]) for call in runner.calls] == [
        (project["project_id"], "default"),
        (project["project_id"], "widgets"),
    ]


def test_two_projects_linking_one_repository_land_onto_its_one_base_branch_and_record(tmp_path: Path) -> None:
    """Nothing per project separates landings: each project's chunk delivers onto the same record's base branch."""
    hub, runner = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme", base_branch="trunk")])
    second = seed_project(hub.engine, "second")
    link_project(hub.engine, project=second, source="default")
    linked = hub.client.put("/api/projects/second/repository-links/widget")
    assert linked.status_code == 200, linked.text
    chunks = [_mint_and_claim(hub, ref, project=project) for ref, project in (("1", "default"), ("2", "second"))]
    for chunk_id, nodes in chunks:
        _seed_at_deliver(hub, chunk_id, nodes, [("widget", None)])

    for chunk_id, nodes in chunks:

        def land(_command: str, chunk_id: str = chunk_id, nodes: dict[str, str] = nodes) -> None:
            hub.services.hub_node.record_marker(
                chunk_id,
                node_id=nodes["deliver"],
                node_name="deliver",
                epoch=1,
                name="merged/acme/widget",
                content="c1",
            )

        runner.before_run = land
        _visit(hub, chunk_id, nodes)

    assert {
        (env["BZ_HUB_PROJECT"], env["BZ_FORGE_OWNER"], env["BZ_HUB_BASE_BRANCH"]) for _, _, env in runner.calls
    } == {
        ("default", "acme", "trunk"),
        ("second", "acme", "trunk"),
    }
    landed = schema.delivery_repo_landed
    with hub.engine.connect() as conn:
        rows = (
            conn.execute(
                sa.select(landed.c.chunk_id, landed.c.repo, landed.c.repository_name).where(
                    landed.c.tenant_id == default_tenant(hub.engine)
                )
            )
            .tuples()
            .all()
        )
    assert sorted(rows) == sorted((chunk_id, "acme/widget", "widget") for chunk_id, _ in chunks)
