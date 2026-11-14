"""A parked watch behind a repository resolved without code, driven through real turns of the hub's flow driver and
the real ``land`` action against the in-memory GitHub double (component tier).

Landing order is A (no code to land), B (parked, its change already opened) and C (unfinished, automatic): the watch
polls B alone, so C's forge is neither checked for a capability, handed a credential, nor called."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert, select

from blizzard.foundation.landing_policy import LandingPolicy
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.chunk.no_code import no_code_marker
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.forges import ConfiguredForge
from blizzard.hub.domain.config.repositories import RepositoryEdit, RepositoryId
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.forge.landing import ChangeState
from blizzard.hub.store import schema as s
from tests.support.forge_auth import retire_secrets
from tests.support.github_double import GitHubDouble, change, forge_holding
from tests.support.hub_node_artifacts import record_hub_asset
from tests.support.scaffolding import (
    OP,
    HubHarness,
    RepositorySeed,
    build_hub,
    default_project,
    hub_scoped,
    hub_store_connections,
    seed_work_item,
    stamp,
)

_COMMIT = "c" * 40
_BRANCH = "feat/x"
_A, _B, _C = "acme/alpha", "acme/bravo", "acme/charlie"
_B_PR = 7
_EPOCH = 1
_GRAPH = """
name: parked-watch
entry: deliver
nodes:
  deliver:
    executor: hub
    action: land
    judgement:
      choices:
        landed: {description: landed., to: done}
        conflict: {description: conflict., to: done}
        inherited-failure: {description: inherited., to: done}
        failure: {description: failure., to: done}
"""


class _World:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.double: GitHubDouble = forge_holding(_A, _B, _C, branch=_BRANCH, commit=_COMMIT)
        self.double.repo(_B).changes[_B_PR] = change(_B, _B_PR, branch=_BRANCH, sha=_COMMIT, base="main")
        seeds = tuple(
            RepositorySeed(
                api_base="http://forge.one",
                owner="acme",
                repo=name.split("/")[1],
                base_branch="main",
                secret_name="charlie-token" if name == _C else "fixture-forge-token",
            )
            for name in (_A, _B, _C)
        )
        self.hub: HubHarness = build_hub(tmp_path, repositories=seeds, forges=self.double.forges)
        self.monkeypatch = monkeypatch
        self.revealed_c: list[str] = []
        self.capability_asked_c: list[str] = []
        records = {f"{r.fields.owner}/{r.fields.repo}": r for r in self.repositories()}
        self.ids = {name: str(record.repository_id) for name, record in records.items()}
        authoring = self.hub.services.config_authoring
        authoring.edit_repository(records[_B], RepositoryEdit(landing_policy=LandingPolicy.PARK), OP)
        project = self.hub.services.projects.get(ProjectId(default_project(self.hub.engine)))
        assert project is not None
        self.hub.services.project_authoring.order_repositories(project, [records[n] for n in (_A, _B, _C)], OP)
        self.chunk_id = self._chunk()
        self.deliver_node = self._deliver_node()

    def repositories(self):
        return self.hub.services.repository_records.list_all(include_retired=False)

    def _chunk(self) -> str:
        minted = self.hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH})
        assert minted.status_code == 201, minted.text
        item = seed_work_item(
            hub_store_connections(self.hub.engine),
            graph_id=minted.json()["graph_id"],
            author=WorkItemAuthor.user("u_1"),
            at=self.hub.clock.now(),
        )
        chunk_id = f"ch_{item.ref}"
        with self.hub.engine.begin() as conn:
            for index, repo in enumerate((_A, _B, _C)):
                conn.execute(
                    insert(s.artifacts).values(
                        stamp(
                            s.artifacts,
                            artifact_id=f"ar_{chunk_id}_{index}",
                            chunk_id=chunk_id,
                            node_id="nd_build",
                            node_name="build",
                            epoch=0,
                            name=f"w{index}",
                            kind="git_commit",
                            data=f"{_BRANCH}:{_COMMIT}",
                            repo=repo,
                            forge=None,
                            produced_at=self.hub.clock.now(),
                            seq=index + 1,
                        )
                    )
                )
        self.hub.stand_at(chunk_id, "deliver")
        return chunk_id

    def _deliver_node(self) -> str:
        chunk = self.hub.services.chunks.record.get(self.chunk_id)
        assert chunk is not None
        graph = self.hub.services.graphs.get(chunk.graph_id)
        assert graph is not None
        node = graph.node_by_name("deliver")
        assert node is not None
        return node.node_id

    def record(self, name: str, content: str) -> None:
        """Record ``name`` on the deliver visit through the artifacts write seam."""
        assert record_hub_asset(
            self.hub,
            self.chunk_id,
            node_id=self.deliver_node,
            node_name="deliver",
            epoch=_EPOCH,
            name=name,
            content=content,
        )

    def a_needs_no_code(self) -> None:
        self.record(
            *no_code_marker(
                {"repo": _A, "record": self.ids[_A], "base_branch": "main", "branch": _BRANCH, "commit": _COMMIT}
            )
        )

    def b_was_opened(self) -> None:
        self.record(f"delivery-pr/{_B}/{_B_PR}", f'{{"repo": "{_B}", "number": {_B_PR}}}')

    def c_unavailable(self) -> None:
        """C's credential raises when revealed, and C's forge offers nothing."""
        forges = self.hub.services.forges
        offered = forges.offered

        def retired(name: str) -> bool:
            if name == "charlie-token":
                self.revealed_c.append(name)
                return True
            return False

        def lacking(forge: ConfiguredForge, repository: RepositoryId | None = None) -> frozenset[Capability]:
            if repository is not None and str(repository) == self.ids[_C]:
                self.capability_asked_c.append(str(repository))
                return frozenset()
            return offered(forge, repository)

        retire_secrets(self.monkeypatch, forges.authorizations, retired)
        self.monkeypatch.setattr(forges, "offered", lacking)

    def c_available(self) -> None:
        self.monkeypatch.undo()

    def turn(self) -> str | None:
        outcome = self.hub.turn_outcome(self.chunk_id)
        self.hub.clock.advance(timedelta(hours=1))
        return outcome

    def artifact(self, name: str) -> str | None:
        rows = self.hub.services.chunks.artifacts.load_artifacts(self.chunk_id)
        return next((a.data for a in rows if a.name == name), None)

    def landed(self) -> list[tuple[str, str]]:
        table = s.delivery_repo_landed
        with self.hub.engine.begin() as conn:
            rows = conn.execute(
                hub_scoped(select(table.c.repo, table.c.commit_hash)).where(table.c.chunk_id == self.chunk_id)
            ).all()
        return sorted((row[0], row[1]) for row in rows)

    def called(self, repo: str) -> list[str]:
        return [name for name, called, _ in self.double.calls if called == repo]


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _World:
    built = _World(tmp_path, monkeypatch)
    built.a_needs_no_code()
    built.b_was_opened()
    return built


def test_the_watch_behind_a_no_code_repository_polls_only_the_parked_one_then_lands_the_rest(world: _World) -> None:
    world.c_unavailable()

    assert world.turn() == "awaiting-merge"
    assert world.called(_B)
    assert world.called(_A) == [] and world.called(_C) == []
    assert world.revealed_c == [] and world.capability_asked_c == []
    assert world.artifact("hub-log.capability-missing") is None
    assert world.artifact("hub-log.repository-secret-unavailable") is None

    world.double.repo(_B).changes[_B_PR] = change(
        _B, _B_PR, branch=_BRANCH, sha=_COMMIT, base="main", state=ChangeState.MERGED, merged_revision="merged-b"
    )
    assert world.turn() == "pending"
    assert world.landed() == [(_B, "merged-b")]
    assert world.artifact(f"merged/{_B}") == "merged-b"
    assert world.called(_C) == [] and world.revealed_c == [] and world.capability_asked_c == []

    world.c_available()
    assert world.turn() == "landed"
    assert world.landed() == [(_B, "merged-b"), (_C, f"merge-{_C}-1")]
    assert [repo for repo, _ in world.double.named("open_change")] == [_C]
    assert world.called(_A) == []


def test_a_current_epoch_reopen_of_the_parked_repository_sends_the_next_turn_down_the_ordinary_path(
    world: _World,
) -> None:
    world.record(f"park-reopen/{_B}/{_B_PR}", "")
    world.c_unavailable()

    world.turn()

    # Outside the watch every repository's forge is checked, so C's missing capability is met.
    assert world.capability_asked_c == [world.ids[_C]]
    assert world.double.named("open_change") == []
