"""A hub node visit resolves its chunk's commits to repository records before its action's pass, and hands
``land`` one forge handle per record (component tier)."""

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
from blizzard.hub.delivery.forge_handle import IGitHubHandle
from blizzard.hub.documents import YAML_CODEC
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.config.repositories import ResolvedRepository
from blizzard.hub.domain.config.secrets import SecretName, SecretRetired
from blizzard.hub.graphs import PACKAGED
from blizzard.hub.store import schema
from tests.github_double import GitHubDouble
from tests.support import (
    FIXTURE_FORGE_SECRET,
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
_COMMIT = "a" * 40


def _mint_and_claim(hub: HubHarness, ref: str = "1", *, project: str | None = None) -> tuple[str, dict[str, str]]:
    minted = hub.client.post(
        "/api/graphs",
        json={"definition_yaml": PACKAGED.named("advanced-development-workflow").inlined_yaml(YAML_CODEC)},
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
            data=f"feat/x:{_COMMIT}",
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


def _ids(hub: HubHarness) -> dict[str, str]:
    """Each repository's id by slug."""
    listed = hub.client.get("/api/repositories").json()["repositories"]
    return {r["slug"]: r["repository_id"] for r in listed}


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


class _Forge:
    """The ``GitHubDouble`` behind every handle, and each handle built — the record it was built for, as
    ``(slug, record id, base branch, forge url, owner, token)``."""

    def __init__(self) -> None:
        self.double = GitHubDouble()
        self.built: list[tuple[str, str, str, str, str, str]] = []

    def handle(self, target: ResolvedRepository, token: str) -> IGitHubHandle:
        self.built.append((target.slug, target.record, target.base_branch, target.forge_api_url, target.owner, token))
        return self.double.handle(target, token)

    def push(self, *repos: str) -> None:
        """Push the chunk's branch to each ``owner/repo``, so ``land`` opens and merges a PR for it."""
        for repo in repos:
            self.double.repo(repo).heads["feat/x"] = _COMMIT


def _hub(tmp_path: Path) -> tuple[HubHarness, _Forge]:
    forge = _Forge()
    return build_hub(tmp_path, forge_handles=forge.handle, repositories=()), forge


def _landed(hub: HubHarness, chunk_id: str | None = None) -> list[tuple[str, str, str | None]]:
    landed = schema.delivery_repo_landed
    query = sa.select(landed.c.chunk_id, landed.c.repo, landed.c.repository_id).where(
        landed.c.tenant_id == default_tenant(hub.engine)
    )
    if chunk_id is not None:
        query = query.where(landed.c.chunk_id == chunk_id)
    with hub.engine.connect() as conn:
        return [tuple(row) for row in conn.execute(query).tuples().all()]


def _artifact_names(hub: HubHarness, chunk_id: str) -> list[str]:
    return [a["name"] for a in hub.client.get(f"/api/chunks/{chunk_id}").json()["artifacts"]]


def test_a_resolved_visit_hands_land_a_handle_built_from_the_repository(tmp_path: Path) -> None:
    hub, forge = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme", base_branch="trunk")])
    forge.push("acme/widget")
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None)])

    assert _visit(hub, chunk_id, nodes).outcome_choice == "landed"

    assert forge.built == [("widget", _ids(hub)["widget"], "trunk", _FORGE, "acme", "fixture-token")]
    assert [args["base"] for _, args in forge.double.named("open_pull")] == ["trunk"]


def test_a_secret_replaced_between_visits_reaches_the_next_visit(tmp_path: Path) -> None:
    hub, forge = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme")])
    forge.push("acme/widget")
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None)])

    _visit(hub, chunk_id, nodes)
    replaced = hub.client.put(f"/api/secrets/{FIXTURE_FORGE_SECRET}/value", json={"value": "rotated-token"})
    assert replaced.status_code == 200, replaced.text
    # The first visit landed the chunk, so the next visit is a second chunk at `deliver`.
    next_chunk_id, next_nodes = _mint_and_claim(hub, "2")
    _seed_at_deliver(hub, next_chunk_id, next_nodes, [("widget", None)])
    _visit(hub, next_chunk_id, next_nodes)

    assert [built[5] for built in forge.built] == ["fixture-token", "rotated-token"]


def test_an_unresolved_repository_reaches_no_forge_routes_failure_and_is_recorded(tmp_path: Path) -> None:
    hub, forge = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme")])
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("gadget", None)])

    result = _visit(hub, chunk_id, nodes)

    assert result.outcome_choice == "failure"
    assert forge.built == [] and forge.double.calls == []
    assert "repository-unresolved" in _event_kinds(hub)
    assert "hub-log.repository-unresolved" in _artifact_names(hub, chunk_id)


def test_a_repository_retired_after_the_chunk_minted_still_resolves_but_a_chunk_minted_after_does_not(
    tmp_path: Path,
) -> None:
    hub, forge = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme")])
    forge.push("acme/widget")
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

    assert _visit(hub, earlier, earlier_nodes).outcome_choice == "landed"
    assert len(forge.built) == 1
    assert _visit(hub, later, earlier_nodes).outcome_choice == "failure"
    assert len(forge.built) == 1
    assert "repository-unresolved" in _event_kinds(hub)


def _second_secret(hub: HubHarness) -> None:
    created = hub.client.post("/api/secrets", json={"name": "other-forge-token", "value": "other-token"})
    assert created.status_code == 201, created.text


def test_records_differing_in_base_branch_each_get_their_own_handle(tmp_path: Path) -> None:
    hub, forge = _hub(tmp_path)
    create_repositories(
        hub.client,
        [
            fixture_repository("widget", _FORGE, owner="acme"),
            fixture_repository("gadget", _FORGE, owner="acme", base_branch="develop"),
        ],
    )
    forge.push("acme/widget", "acme/gadget")
    forge.double.repo("acme/gadget").base_branch = "develop"
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None), ("gadget", None)])

    assert _visit(hub, chunk_id, nodes).outcome_choice == "landed"

    ids = _ids(hub)
    assert sorted(forge.built) == sorted(
        [
            ("widget", ids["widget"], "main", _FORGE, "acme", "fixture-token"),
            ("gadget", ids["gadget"], "develop", _FORGE, "acme", "fixture-token"),
        ]
    )
    assert sorted((repo, args["base"]) for repo, args in forge.double.named("open_pull")) == [
        ("acme/gadget", "develop"),
        ("acme/widget", "main"),
    ]
    assert "repositories-disagree" not in _event_kinds(hub)


def test_records_differing_in_secret_each_get_their_own_token(tmp_path: Path) -> None:
    hub, forge = _hub(tmp_path)
    _second_secret(hub)
    create_repositories(
        hub.client,
        [
            fixture_repository("widget", _FORGE, owner="acme"),
            fixture_repository("gadget", _FORGE, owner="acme") | {"secret_name": "other-forge-token"},
        ],
    )
    forge.push("acme/widget", "acme/gadget")
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None), ("gadget", None)])

    _visit(hub, chunk_id, nodes)

    assert forge.double.tokens == {_ids(hub)["widget"]: "fixture-token", _ids(hub)["gadget"]: "other-token"}


def test_an_action_that_lands_nothing_is_handed_no_forge(tmp_path: Path) -> None:
    hub, forge = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme")])
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None)])

    _visit(hub, chunk_id, nodes, "record-findings")

    assert forge.built == []


def test_an_unavailable_secret_of_any_record_refuses_the_visit_naming_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, forge = _hub(tmp_path)
    _second_secret(hub)
    create_repositories(
        hub.client,
        [
            fixture_repository("widget", _FORGE, owner="acme"),
            fixture_repository("gadget", _FORGE, owner="acme") | {"secret_name": "other-forge-token"},
        ],
    )
    reveal = hub.services.hub_node._secrets.reveal

    def reveal_unless_retired(name: SecretName):  # type: ignore[no-untyped-def]
        if name.value == "other-forge-token":
            raise SecretRetired("other-forge-token")
        return reveal(name)

    monkeypatch.setattr(hub.services.hub_node._secrets, "reveal", reveal_unless_retired)
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None), ("gadget", None)])

    result = _visit(hub, chunk_id, nodes)

    assert result.outcome_choice == "failure"
    assert forge.built == [] and forge.double.calls == []
    assert "hub-log.repository-secret-unavailable" in _artifact_names(hub, chunk_id)


def _two_hosts_one_name(hub: HubHarness) -> None:
    create_repositories(
        hub.client,
        [
            fixture_repository("widget", "https://api.github.com", owner="acme"),
            fixture_repository("widget-mirror", "https://git.example.test/api/v3", owner="acme", base_branch="develop")
            | {"repo": "widget"},
        ],
    )


_GITHUB_WIDGET = ("widget", "git@github.com:acme/widget.git")
_MIRROR_WIDGET = ("widget", "https://git.example.test/acme/widget")


def test_one_owner_and_name_on_two_forge_hosts_is_refused_before_land_naming_both_records(tmp_path: Path) -> None:
    hub, forge = _hub(tmp_path)
    _two_hosts_one_name(hub)
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [_GITHUB_WIDGET, _MIRROR_WIDGET])

    result = _visit(hub, chunk_id, nodes)

    assert result.outcome_choice == "failure"
    assert forge.built == []
    log = next(
        a.data
        for a in hub.services.chunks.artifacts.load_artifacts(chunk_id)
        if a.name == "hub-log.repository-ambiguous"
    )
    ids = _ids(hub)
    assert "resolves to two repository records" in log
    assert ids["widget"] in log and ids["widget-mirror"] in log
    assert "merged/acme/widget" not in _artifact_names(hub, chunk_id)


def test_a_chunk_with_no_commits_reaches_no_forge_and_fails(tmp_path: Path) -> None:
    hub, forge = _hub(tmp_path)
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [])

    assert _visit(hub, chunk_id, nodes).outcome_choice == "failure"

    assert forge.built == [] and forge.double.calls == []


def test_a_name_ambiguous_across_the_tenant_lands_in_the_record_its_project_links(tmp_path: Path) -> None:
    hub, forge = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme")], project=None)
    fork = fixture_repository("widget", _FORGE, owner="other") | {"slug": "widget-fork"}
    create_repositories(hub.client, [fork])
    forge.push("other/widget")
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None)])

    assert _visit(hub, chunk_id, nodes).outcome_choice == "landed"

    assert [(slug, owner) for slug, _, _, _, owner, _ in forge.built] == [("widget-fork", "other")]
    assert [repo for repo, _ in forge.double.named("merge")] == ["other/widget"]


def test_a_repository_its_project_does_not_link_reaches_no_forge_and_is_recorded_as_not_linked(
    tmp_path: Path,
) -> None:
    hub, forge = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme")], project=None)
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None)])

    result = _visit(hub, chunk_id, nodes)

    assert forge.built == []
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
    assert "hub-log.repository-not-linked" in _artifact_names(hub, chunk_id)


def test_a_hub_node_that_lands_nothing_still_refuses_a_repository_its_project_does_not_link(tmp_path: Path) -> None:
    hub, forge = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme")], project=None)
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None)])

    result = _visit(hub, chunk_id, nodes, "record-findings")

    assert forge.built == []
    assert result.outcome_choice == "failure"
    assert "repository-not-linked" in _event_kinds(hub)


def test_a_landing_names_the_record_its_visit_started_against_though_unlinked_mid_pass(tmp_path: Path) -> None:
    hub, forge = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme")])
    forge.push("acme/widget")
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver(hub, chunk_id, nodes, [("widget", None)])

    def unlink(_number: int) -> None:
        unlinked = hub.client.post("/api/projects/default/repository-links/widget/retire")
        assert unlinked.status_code == 200, unlinked.text

    forge.double.repo("acme/widget").on_merge = unlink
    assert _visit(hub, chunk_id, nodes).outcome_choice == "landed"

    assert _landed(hub, chunk_id) == [(chunk_id, "acme/widget", _ids(hub)["widget"])]


def test_two_projects_linking_one_repository_land_onto_its_one_base_branch_and_record(tmp_path: Path) -> None:
    """Nothing per project separates landings: each project's chunk delivers onto the same record's base branch."""
    hub, forge = _hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("widget", _FORGE, owner="acme", base_branch="trunk")])
    second = seed_project(hub.engine, "second")
    link_project(hub.engine, project=second, source="default")
    linked = hub.client.put("/api/projects/second/repository-links/widget")
    assert linked.status_code == 200, linked.text
    chunks = [_mint_and_claim(hub, ref, project=project) for ref, project in (("1", "default"), ("2", "second"))]
    for chunk_id, nodes in chunks:
        _seed_at_deliver(hub, chunk_id, nodes, [("widget", None)])

    for chunk_id, nodes in chunks:
        # Each chunk's branch is pushed afresh: the first landing merged and closed the PR for it.
        forge.double.repo("acme/widget").pulls.clear()
        forge.push("acme/widget")
        assert _visit(hub, chunk_id, nodes).outcome_choice == "landed"

    assert {(slug, owner, base) for slug, _, base, _, owner, _ in forge.built} == {("widget", "acme", "trunk")}
    assert [args["base"] for _, args in forge.double.named("open_pull")] == ["trunk", "trunk"]
    assert sorted(_landed(hub)) == sorted((chunk_id, "acme/widget", _ids(hub)["widget"]) for chunk_id, _ in chunks)
