"""A chunk landing several repositories lands them in its project's landing order, as its first turn took it.

Against the mock forge, on ``test_flow_turns_e2e``'s two-repository world (``toy-api``/``toy-web``): a repository
ranked first lands first; a second project lands the same repositories in its own reverse order; ranked repositories
land before unranked ones, which fall back to slug order; a relinked repository keeps its position; a gap exhausted by
moves is renumbered; a reorder reaches only chunks whose first turn comes after it, across a hub restart too; and an
exported configuration document applies back writing nothing. Each scenario ends with the landing invariants holding.
Needs ``BLIZZARD_E2E=1`` and ``blizzard-mock``."""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx2
import pytest

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import ProjectBinding, RunnerConfig
from blizzard.runner.environments.provider import WorkspaceRepo
from blizzard.tools.invariants import hub_invariants
from tests.e2e.flow_support import flow_world, landing_graph_yaml, set_lever, ticking_runner
from tests.e2e.test_flow_turns_e2e import (
    WEB_REPO,
    WEB_REPO_NAME,
    await_landed,
    chunk_detail,
    open_hub_db,
    pending,
)
from tests.fleet_launchers import OWNER, REPO, run_forge, run_hub, runner_config
from tests.fleet_world import REPO_NAME, basic_workspace, world_repos
from tests.support import create_repositories, fixture_repository, fixture_work_source, free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e landing order needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_LIB_REPO_NAME = "toy-lib"
_LIB_REPO = f"{OWNER}/{_LIB_REPO_NAME}"
_BOTH = (REPO_NAME, WEB_REPO_NAME)
_SECOND_PROJECT = "consumers"
_LANDING_INVARIANTS = frozenset(
    {"hub:landing-position-on-link", "hub:submission-terms-standing", "hub:repository-references-in-tenant"}
)


def _chunk(
    hub: httpx2.Client, forge: httpx2.Client, landed_file: str, repos: Sequence[str], *, source: str = REPO_NAME
) -> str:
    """Mint the graph landing ``landed_file`` in ``repos``, ingest one chunk from ``source`` pinned to it, and promote
    it; the chunk belongs to the project ``source`` is linked to."""
    graph = landing_graph_yaml(landed_file, repos, poll_interval=1, record_findings=False)
    assert hub.post("/api/graphs", json={"definition_yaml": graph}).status_code == 201
    issue = forge.post(f"/repos/{OWNER}/{source}/issues", json={"title": landed_file, "body": f"lands {landed_file}"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post("/api/chunks", json={"tokens": [f"{source}:{issue.json()['number']}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    return chunk_id


def _add_web(hub: httpx2.Client, forge_port: int) -> None:
    create_repositories(hub, [fixture_repository(WEB_REPO_NAME, f"http://127.0.0.1:{forge_port}", owner=OWNER)])


def _add_lib(hub: httpx2.Client, forge: httpx2.Client, forge_port: int) -> Path:
    """A third repository, ``toy-lib``, made on the forge and stored and linked at the hub; answers its bare origin."""
    made = forge.post("/_repos", json={"owner": OWNER, "name": _LIB_REPO_NAME})
    assert made.status_code == 201, made.text
    create_repositories(hub, [fixture_repository(_LIB_REPO_NAME, f"http://127.0.0.1:{forge_port}", owner=OWNER)])
    return Path(made.json()["origin"].removeprefix("file://"))


def flow_runner_config(
    tmp_path: Path,
    workspace: Path,
    bin_dir: Path,
    hub_port: int,
    *,
    extra_repos: Sequence[WorkspaceRepo] = (),
    projects: tuple[str, ...] | None = None,
) -> RunnerConfig:
    """A runner with two environments over the world's repositories and ``extra_repos``, serving ``projects`` when
    named and its own declaration otherwise."""
    repos = (*world_repos(workspace), *extra_repos)
    base = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
    config = dataclasses.replace(
        base, workspaces=(dataclasses.replace(basic_workspace(workspace, repos=repos), max_environments=2),)
    )
    if projects is None:
        return config
    (home,) = {binding.workspace for binding in base.bindings or ()}
    return dataclasses.replace(
        config, projects=projects, bindings=tuple(ProjectBinding(project, home) for project in projects)
    )


def _order(hub: httpx2.Client, project: str, *slugs: str) -> None:
    ordered = hub.put(f"/api/projects/{project}/landing-order", json={"repositories": list(slugs)})
    assert ordered.status_code == 200, ordered.text


def _move(hub: httpx2.Client, project: str, slug: str, *, after: str | None) -> list[tuple[str, float | None]]:
    moved = hub.post(
        f"/api/projects/{project}/landing-order/position", json={"repository": slug, "after_repository": after}
    )
    assert moved.status_code == 200, moved.text
    return [(link["slug"], link["position"]) for link in moved.json()["repository_links"]]


def _links(hub: httpx2.Client, project: str) -> list[tuple[str, float | None]]:
    listed = hub.get(f"/api/projects/{project}/repository-links")
    assert listed.status_code == 200, listed.text
    return [(link["slug"], link["position"]) for link in listed.json()["repository_links"]]


def _landed_order(hub_dir: Path, chunk_id: str) -> list[str]:
    """The repositories the chunk landed, by name, in the order their landings were written."""
    with open_hub_db(hub_dir) as conn:
        rows = conn.execute(
            "SELECT repo FROM delivery_repo_landed WHERE chunk_id = ? ORDER BY id", (chunk_id,)
        ).fetchall()
    return [repo.rpartition("/")[2] for (repo,) in rows]


def _terms(hub_dir: Path, chunk_id: str) -> dict[str, tuple[str, float | None]]:
    """The terms the chunk's submissions took, by repository slug: ``(landing_policy, landing_position)``."""
    with open_hub_db(hub_dir) as conn:
        rows = conn.execute(
            "SELECT r.slug, t.landing_policy, t.landing_position FROM flow_submission_terms t"
            " JOIN flow_submissions s ON s.submission_id = t.submission_id"
            " JOIN repositories r ON r.repository_id = t.repository_id WHERE s.chunk_id = ?",
            (chunk_id,),
        ).fetchall()
    return {slug: (policy, position) for slug, policy, position in rows}


def _position_facts(hub_dir: Path) -> int:
    with open_hub_db(hub_dir) as conn:
        return conn.execute("SELECT count(*) FROM project_repository_positions").fetchone()[0]


def _assert_landing_record_holds(hub_dir: Path) -> None:
    """The landing position, the submission terms, and the tenant's repository references are all sound."""
    engine = create_engine_from_url(f"sqlite:///{hub_dir / 'data' / 'hub.db'}")
    try:
        violations = [v for v in hub_invariants(engine).run() if v.invariant in _LANDING_INVARIANTS]
    finally:
        engine.dispose()
    assert violations == []


def _release(forge: httpx2.Client, *repos: str) -> None:
    for repo in repos:
        set_lever(forge, repo, armed=False)


def test_a_library_ranked_first_lands_before_its_consumer(tmp_path: Path) -> None:
    """Ranked ahead of ``toy-api`` — against slug order — ``toy-web`` lands first."""
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        _add_web(hub, forge_port)
        _order(hub, "default", WEB_REPO_NAME, REPO_NAME)
        assert _links(hub, "default") == [(WEB_REPO_NAME, 1.0), (REPO_NAME, 2.0)]
        with ticking_runner(flow_runner_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            chunk = _chunk(hub, forge, "LIBRARY.md", _BOTH)
            tick_until(lambda: chunk_detail(hub, chunk)["status"] in {"done", "needs_human", "stopped"})
        await_landed(hub, chunk)

        assert _landed_order(hub_dir, chunk) == [WEB_REPO_NAME, REPO_NAME]
        assert _terms(hub_dir, chunk) == {
            WEB_REPO_NAME: ("merge-on-checks", 1.0),
            REPO_NAME: ("merge-on-checks", 2.0),
        }
        _assert_landing_record_holds(hub_dir)


def test_a_second_project_lands_the_same_repositories_in_its_own_order(tmp_path: Path) -> None:
    """Two projects linking both repositories in reverse orders each land their own chunk in their own order."""
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()
    forge_api = f"http://127.0.0.1:{forge_port}"

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        _add_web(hub, forge_port)
        created = hub.post("/api/projects", json={"slug": _SECOND_PROJECT, "name": "Consumers", "description": ""})
        assert created.status_code == 201, created.text
        source = fixture_work_source(WEB_REPO_NAME, WEB_REPO, forge_api)
        assert hub.post("/api/work-sources", json=source.model_dump()).status_code == 201
        assert hub.put(f"/api/projects/{_SECOND_PROJECT}/source-links/{WEB_REPO_NAME}").status_code == 200
        for slug in _BOTH:
            assert hub.put(f"/api/projects/{_SECOND_PROJECT}/repository-links/{slug}").status_code == 200
        _order(hub, "default", WEB_REPO_NAME, REPO_NAME)
        _order(hub, _SECOND_PROJECT, REPO_NAME, WEB_REPO_NAME)

        config = flow_runner_config(tmp_path, workspace, bin_dir, hub_port, projects=("default", _SECOND_PROJECT))
        with ticking_runner(config) as tick_until:
            first = _chunk(hub, forge, "FIRST-PROJECT.md", _BOTH)
            second = _chunk(hub, forge, "SECOND-PROJECT.md", _BOTH, source=WEB_REPO_NAME)
            assert chunk_detail(hub, second)["project_id"] == created.json()["project_id"]
            settled = {"done", "needs_human", "stopped"}
            tick_until(lambda: all(chunk_detail(hub, c)["status"] in settled for c in (first, second)))
        await_landed(hub, first, second)

        assert _landed_order(hub_dir, first) == [WEB_REPO_NAME, REPO_NAME]
        assert _landed_order(hub_dir, second) == [REPO_NAME, WEB_REPO_NAME]
        _assert_landing_record_holds(hub_dir)


def test_one_ranked_repository_lands_before_two_unranked_in_slug_order(tmp_path: Path) -> None:
    """With only ``toy-web`` ranked, it lands first, then ``toy-api`` and ``toy-lib`` by slug."""
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()
    three = (REPO_NAME, _LIB_REPO_NAME, WEB_REPO_NAME)

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        _add_web(hub, forge_port)
        lib_origin = _add_lib(hub, forge, forge_port)
        _order(hub, "default", WEB_REPO_NAME)
        assert _links(hub, "default") == [(WEB_REPO_NAME, 1.0), (REPO_NAME, None), (_LIB_REPO_NAME, None)]

        extra = (WorkspaceRepo(_LIB_REPO_NAME, f"file://{lib_origin}"),)
        with ticking_runner(
            flow_runner_config(tmp_path, workspace, bin_dir, hub_port, extra_repos=extra)
        ) as tick_until:
            chunk = _chunk(hub, forge, "RANKED.md", three)
            tick_until(lambda: chunk_detail(hub, chunk)["status"] in {"done", "needs_human", "stopped"})
        await_landed(hub, chunk)

        assert _landed_order(hub_dir, chunk) == [WEB_REPO_NAME, REPO_NAME, _LIB_REPO_NAME]
        _assert_landing_record_holds(hub_dir)


def test_a_relinked_repository_keeps_its_position(tmp_path: Path) -> None:
    """Unlinking a ranked repository drops it from the order; relinking it restores its position, and a chunk lands
    in that order."""
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        _add_web(hub, forge_port)
        _order(hub, "default", WEB_REPO_NAME, REPO_NAME)
        unlinked = hub.post(f"/api/projects/default/repository-links/{WEB_REPO_NAME}/retire")
        assert unlinked.status_code == 200, unlinked.text
        assert _links(hub, "default") == [(REPO_NAME, 2.0)]
        _assert_landing_record_holds(hub_dir)
        assert hub.put(f"/api/projects/default/repository-links/{WEB_REPO_NAME}").status_code == 200
        assert _links(hub, "default") == [(WEB_REPO_NAME, 1.0), (REPO_NAME, 2.0)]

        with ticking_runner(flow_runner_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            chunk = _chunk(hub, forge, "RELINKED.md", _BOTH)
            tick_until(lambda: chunk_detail(hub, chunk)["status"] in {"done", "needs_human", "stopped"})
        await_landed(hub, chunk)

        assert _landed_order(hub_dir, chunk) == [WEB_REPO_NAME, REPO_NAME]
        _assert_landing_record_holds(hub_dir)


def test_an_exhausted_gap_is_renumbered_and_the_order_lands(tmp_path: Path) -> None:
    """Moving two repositories in turn just after a third halves the gap until no midpoint is left; the next move
    renumbers the order ``1.0, 2.0, …`` and takes the midpoint, and a chunk lands in the order that leaves."""
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()
    three = (REPO_NAME, _LIB_REPO_NAME, WEB_REPO_NAME)

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        _add_web(hub, forge_port)
        lib_origin = _add_lib(hub, forge, forge_port)
        _order(hub, "default", WEB_REPO_NAME, _LIB_REPO_NAME, REPO_NAME)

        renumbered: list[tuple[str, float | None]] | None = None
        moving, still = REPO_NAME, _LIB_REPO_NAME
        halvings = 0
        while renumbered is None and halvings < 200:
            order = _move(hub, "default", moving, after=WEB_REPO_NAME)
            if halvings > 0 and order[1][1] == 1.5:
                renumbered = order
            else:
                moving, still = still, moving
                halvings += 1
        # A float gap of one halves some fifty times before no midpoint is left.
        assert renumbered is not None and halvings > 40, (halvings, renumbered)
        assert renumbered == [(WEB_REPO_NAME, 1.0), (moving, 1.5), (still, 2.0)]
        expected = [slug for slug, _ in renumbered]

        extra = (WorkspaceRepo(_LIB_REPO_NAME, f"file://{lib_origin}"),)
        with ticking_runner(
            flow_runner_config(tmp_path, workspace, bin_dir, hub_port, extra_repos=extra)
        ) as tick_until:
            chunk = _chunk(hub, forge, "RENUMBERED.md", three)
            tick_until(lambda: chunk_detail(hub, chunk)["status"] in {"done", "needs_human", "stopped"})
        await_landed(hub, chunk)

        assert _landed_order(hub_dir, chunk) == expected
        _assert_landing_record_holds(hub_dir)


def test_a_reorder_while_a_chunk_waits_behind_another_reaches_only_later_chunks(tmp_path: Path) -> None:
    """Two chunks on both repositories wait behind the checks, the second behind the first; a reorder then reaches
    neither — both land in the order their first turns took — while a chunk whose first turn follows it lands in the
    new order."""
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        _add_web(hub, forge_port)
        _order(hub, "default", WEB_REPO_NAME, REPO_NAME)
        for repo in (REPO, WEB_REPO):
            set_lever(forge, repo, armed=True)
        config = flow_runner_config(tmp_path, workspace, bin_dir, hub_port)
        with ticking_runner(config) as tick_until:
            ahead = _chunk(hub, forge, "AHEAD.md", _BOTH)
            tick_until(pending(hub, ahead))
            behind = _chunk(hub, forge, "BEHIND.md", _BOTH)
            tick_until(pending(hub, behind))
        _order(hub, "default", REPO_NAME, WEB_REPO_NAME)
        _assert_landing_record_holds(hub_dir)

        _release(forge, REPO, WEB_REPO)
        await_landed(hub, ahead, behind)
        assert _landed_order(hub_dir, ahead) == [WEB_REPO_NAME, REPO_NAME]
        assert _landed_order(hub_dir, behind) == [WEB_REPO_NAME, REPO_NAME]

        with ticking_runner(config) as tick_until:
            later = _chunk(hub, forge, "LATER.md", _BOTH)
            tick_until(lambda: chunk_detail(hub, later)["status"] in {"done", "needs_human", "stopped"})
        await_landed(hub, later)
        assert _landed_order(hub_dir, later) == [REPO_NAME, WEB_REPO_NAME]
        _assert_landing_record_holds(hub_dir)


def test_an_interrupted_turn_keeps_the_terms_its_first_turn_took(tmp_path: Path) -> None:
    """A chunk waiting behind the checks after its first turn is reordered under; the hub restarts with the checks
    green, and the chunk lands in the order it first took."""
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge:
        with run_hub(hub_dir, forge_port, hub_port) as hub:
            _add_web(hub, forge_port)
            _order(hub, "default", WEB_REPO_NAME, REPO_NAME)
            for repo in (REPO, WEB_REPO):
                set_lever(forge, repo, armed=True)
            with ticking_runner(flow_runner_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
                chunk = _chunk(hub, forge, "INTERRUPTED.md", _BOTH)
                tick_until(pending(hub, chunk))
            taken = _terms(hub_dir, chunk)
            _order(hub, "default", REPO_NAME, WEB_REPO_NAME)
            assert _links(hub, "default") == [(REPO_NAME, 1.0), (WEB_REPO_NAME, 2.0)]
        assert taken == {WEB_REPO_NAME: ("merge-on-checks", 1.0), REPO_NAME: ("merge-on-checks", 2.0)}
        # Down while the checks go green, so the visit is due on the first tick back.
        _release(forge, REPO, WEB_REPO)
        with run_hub(hub_dir, forge_port, hub_port, rehost=True) as hub:
            await_landed(hub, chunk)

    assert _terms(hub_dir, chunk) == taken
    assert _landed_order(hub_dir, chunk) == [WEB_REPO_NAME, REPO_NAME]
    _assert_landing_record_holds(hub_dir)


def _written(applied: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(o["kind"], o["key"], o["op"]) for o in applied["outcomes"] if o["op"] != "unchanged"]


def test_an_exported_document_applies_back_writing_nothing(tmp_path: Path) -> None:
    """A hub with a ranked and an unranked repository exports its configuration; applying the document back writes
    no record and no position."""
    bin_dir, _, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        _add_web(hub, forge_port)
        _add_lib(hub, forge, forge_port)
        _order(hub, "default", WEB_REPO_NAME, REPO_NAME)
        exported = hub.get("/api/config/export")
        assert exported.status_code == 200, exported.text
        document = exported.json()
        (default,) = [p for p in document["projects"] if p["slug"] == "default"]
        assert default["landing_order"] == [WEB_REPO_NAME, REPO_NAME]
        facts = _position_facts(hub_dir)

        applied = hub.post("/api/config/apply", json=document)
        assert applied.status_code == 200, applied.text

        assert _written(applied.json()) == []
        assert _position_facts(hub_dir) == facts
        assert _links(hub, "default") == [(WEB_REPO_NAME, 1.0), (REPO_NAME, 2.0), (_LIB_REPO_NAME, None)]
        _assert_landing_record_holds(hub_dir)
