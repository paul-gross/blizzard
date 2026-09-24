"""The registered FILL crash windows with a basic-bound real runner, no winter fixture."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.runner.environments.factory import build_workspace_provider
from blizzard.tools.invariants import Invariants
from tests.crash.support import start_runner, terminate, wait_death, wait_status
from tests.crash.test_kill9_sweep import _abandon_graph_yaml, _await_marker, _leases_for_chunk, _wait_for_closure
from tests.e2e.test_acceptance_loop import REPO, REPO_NAME, _forge, _free_port, _git_bare, _hub, _mock_bin_dir
from tests.e2e.test_basic_workspace import bare_origin, basic_build_prompt, basic_config, basic_graph_yaml, trap_winter
from tests.runner_fakes import SqlAlchemyRunnerStore, runner_store_errors

pytestmark = pytest.mark.crash_sweep


@pytest.mark.parametrize("point", ["fill.after-env-acquire.before-bind", "fill.after-bind.before-claim"])
def test_basic_runner_recovers_from_fill_kill9(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str) -> None:
    if os.environ.get("BLIZZARD_CRASH_SWEEP") != "1":
        pytest.skip("set BLIZZARD_CRASH_SWEEP=1 to drive real daemon subprocesses")
    bin_dir = _mock_bin_dir()
    if bin_dir is None:
        pytest.skip("provision the sibling blizzard-mock worktree")

    winter_invoked = trap_winter(tmp_path, monkeypatch)
    bare = bare_origin(tmp_path)
    workspace = tmp_path / "ordinary-workspace"
    workspace.mkdir()
    (workspace / ".blizzard-mock-harness-fence").write_text("basic crash sweep\n")
    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, bare.parent, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        minted = hub.post("/api/graphs", json={"definition_yaml": basic_graph_yaml()})
        assert minted.status_code == 201, minted.text
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": point, "body": "recover and deliver"})
        assert issue.status_code == 201, issue.text
        created = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert created.status_code == 201, created.text
        chunk_id = created.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        runner_dir = tmp_path / "runner"
        config = basic_config(runner_dir, workspace, bin_dir, hub_port, bare)
        runner_proc = start_runner(runner_dir, crash_point=point)
        try:
            assert wait_death(runner_proc) == -9, f"runner did not reach {point}"
            violations = Invariants(
                runner_db_url=config.db_url, hub_db_url=HubConfig.load(tmp_path / "hub").db_url
            ).run(after_recovery=False)
            assert not violations, violations
            # The unbound directory remains on disk at the earlier FILL point; the
            # resumed daemon must reset/reuse it instead of colliding or evicting a hold.
            assert (workspace / chunk_id / REPO_NAME).is_dir()
            runner_proc = start_runner(runner_dir, crash_point=None)
            status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"}, timeout=120)
            assert status == "done", f"basic runner did not recover from {point}: {status}"
            violations = Invariants(
                runner_db_url=config.db_url, hub_db_url=HubConfig.load(tmp_path / "hub").db_url
            ).run(after_recovery=True)
            assert not violations, violations
        finally:
            terminate(runner_proc)

    commits = _git_bare(bare, "log", "--oneline", "main", "--", "LANDED.md").splitlines()
    assert len(commits) == 1, f"landed {len(commits)} times after {point}: {commits}"
    assert not (workspace / ".winter").exists()
    assert not winter_invoked.exists(), "the basic runner invoked winter"


def test_basic_runner_releases_and_reclaims_after_abandon_kill9(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real release precedes the kill; restart sees no old manifest and reuses a clean folder."""
    if os.environ.get("BLIZZARD_CRASH_SWEEP") != "1":
        pytest.skip("set BLIZZARD_CRASH_SWEEP=1 to drive real daemon subprocesses")
    bin_dir = _mock_bin_dir()
    if bin_dir is None:
        pytest.skip("provision the sibling blizzard-mock worktree")

    winter_invoked = trap_winter(tmp_path, monkeypatch)
    bare = bare_origin(tmp_path)
    workspace = tmp_path / "ordinary-workspace"
    workspace.mkdir()
    (workspace / ".blizzard-mock-harness-fence").write_text("basic release crash\n")
    marker = tmp_path / "hang-once.marker"
    landed_file = "LANDED.md"
    graph = yaml.safe_load(_abandon_graph_yaml(landed_file, marker))
    # Reset the worker-owned branch after reacquiring a fresh detached worktree.
    graph["nodes"]["build"]["prompt"] = basic_build_prompt(
        graph["nodes"]["build"]["prompt"], branch="feature/basic-recovered", reset=True
    )
    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, bare.parent, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        minted = hub.post("/api/graphs", json={"definition_yaml": yaml.safe_dump(graph, sort_keys=False)})
        assert minted.status_code == 201, minted.text
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "basic release", "body": "recover and deliver"})
        assert issue.status_code == 201, issue.text
        created = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert created.status_code == 201, created.text
        chunk_id = created.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        runner_dir = tmp_path / "runner"
        config = basic_config(runner_dir, workspace, bin_dir, hub_port, bare)
        point = "abandon.after-release.before-closure"
        runner_proc = start_runner(runner_dir, crash_point=point)
        try:
            assert wait_status(hub, chunk_id, {"running"}) == "running"
            _await_marker(marker)
            before = _leases_for_chunk(runner_dir, chunk_id)
            assert len(before) == 1
            detached = hub.post(f"/api/chunks/{chunk_id}/detach")
            assert detached.status_code == 202, detached.text
            assert wait_death(runner_proc) == -9, f"runner did not reach {point}"

            engine = create_engine_from_url(config.db_url)
            try:
                store = SqlAlchemyRunnerStore(engine, runner_store_errors())
                assert chunk_id not in store.held_environment_ids()
                provider = build_workspace_provider(config, held_ids=store.held_environment_ids)
                assert provider.repos(chunk_id) == [], "released worktree remained authorized after process death"
            finally:
                engine.dispose()

            runner_proc = start_runner(runner_dir, crash_point=None)
            assert _wait_for_closure(runner_dir, before[0][0]) == "released"
            status = wait_status(hub, chunk_id, {"done", "stopped", "needs_human"}, timeout=120)
            assert status == "done", f"basic runner did not recover from release: {status}"
            assert len(_leases_for_chunk(runner_dir, chunk_id)) == 2
            violations = Invariants(
                runner_db_url=config.db_url, hub_db_url=HubConfig.load(tmp_path / "hub").db_url
            ).run(after_recovery=True)
            assert not violations, violations
        finally:
            terminate(runner_proc)

    commits = _git_bare(bare, "log", "--oneline", "main", "--", landed_file).splitlines()
    assert len(commits) == 1, f"landed {len(commits)} times after release: {commits}"
    assert not winter_invoked.exists(), "the basic runner invoked winter"
