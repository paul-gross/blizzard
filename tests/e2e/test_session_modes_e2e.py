"""Node ``session:`` modes end to end — the `test_session_modes_e2e` scenario of the standing e2e smoke.

Proves a node's authored ``session:`` mode governs which harness session a node-entry
spawn continues across a graph transition, on the real forge + hub + runner rails. The
scenarios below cover ``resume:<node>`` and a named pool.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from blizzard.foundation.store.engine import create_engine_from_url
from tests.e2e.support import _graph_yaml, _sessions_by_node
from tests.fleet_launchers import REPO, drive_until_done, mock_bin_dir, run_forge, run_hub, runner_config
from tests.fleet_world import FIXTURE_ENV, REPO_NAME, mint_world
from tests.runner_fakes import daemon_store
from tests.support import free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e session modes needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]


def _session_turns(workspace: Path, session_id: str) -> int:
    """The mock's persisted ``turns`` for ``session_id`` (spawn + each resume)."""
    state_path = workspace / ".blizzard-mock-harness" / "sessions" / f"{session_id}.json"
    assert state_path.is_file(), f"no persisted mock session state at {state_path}"
    return int(json.loads(state_path.read_text())["turns"])


def test_session_modes_resume_targeted_and_fresh_across_a_cycle(tmp_path: Path) -> None:
    """build (`resume:build`) re-entry resumes its own session; `fresh` review is new each time."""
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")

    scratch = tmp_path / "scratch"
    mint_world(bin_dir, scratch)
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"
    origin_bare = origins / f"{REPO_NAME}.git"

    forge_port, hub_port = free_port(), free_port()
    runner_dir = tmp_path / "runner"
    db_url = ""
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201

        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "session modes", "body": "the chunk"})
        assert issue.status_code == 201, issue.text
        issue_number = issue.json()["number"]
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue_number}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202  # ready for the runner

        config = runner_config(runner_dir, workspace, bin_dir, hub_port)
        db_url = config.db_url
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        status = drive_until_done(config, hub, chunk_id, fenced)

        assert status == "done", f"chunk did not reach done (last status {status!r})"

    # The review-fail cycle ran build TWICE — the precondition for the session assertions
    # below: build was genuinely entered twice.
    build_md = subprocess.run(
        ["git", "--git-dir", str(origin_bare), "show", "main:BUILD.md"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert build_md.count("build pass") == 2, f"expected two build passes on main, got:\n{build_md}"

    # The runner-side truth: which session each node-step lease actually ran under.
    by_node = _sessions_by_node(db_url, chunk_id)
    build_sessions = by_node.get("build", [])
    review_sessions = by_node.get("review", [])
    assert len(build_sessions) == 2, f"build should have two node-step leases (entered twice): {by_node}"
    assert len(review_sessions) == 2, f"review should have two node-step leases (fail then pass): {by_node}"

    # (a) + (c): both build leases carry the SAME session id — the second entry resumed
    # the first's fresh session in place rather than spawning a new one.
    assert len(set(build_sessions)) == 1, (
        f"re-entered build did not resume its own session — got distinct ids {build_sessions}"
    )
    build_session = build_sessions[0]

    # (b): the two `fresh` review visits ran on two DISTINCT sessions, disjoint from build's.
    assert len(set(review_sessions)) == 2, f"fresh review did not get a new session each visit: {review_sessions}"
    assert set(review_sessions).isdisjoint(build_sessions), (
        f"a review session collided with build's: reviews={review_sessions} build={build_session}"
    )

    # Turn count proves build's session was CONTINUED, not merely relabeled: a single
    # visit is 2 turns (spawn + judgement resume); two entries accumulate strictly more.
    assert _session_turns(workspace, build_session) > 2, (
        f"build session {build_session} did not accumulate turns across two entries "
        f"(turns={_session_turns(workspace, build_session)}) — it was not resumed in place"
    )
    for review_session in set(review_sessions):
        assert _session_turns(workspace, review_session) >= 1, f"review session {review_session} has no persisted turns"

    # Why the TARGETED form matters (plan Q4): the chunk's most-recent session overall is
    # the reviewer's, not build's — `resume:build` avoids that wrong inheritance.
    store = daemon_store(create_engine_from_url(db_url))
    build_latest = store.latest_session(chunk_id, "build")
    assert build_latest is not None and build_latest.session_id == build_session
    latest = store.latest_session(chunk_id, None)
    chunk_most_recent = latest.session_id if latest is not None else None
    assert chunk_most_recent in review_sessions, (
        f"the chunk's most-recent session should be a review one, got {chunk_most_recent!r}"
    )
    assert chunk_most_recent != build_session, (
        "targeted resume:build must differ from bare resume here — else the scenario proves nothing"
    )


# Named session pools end to end. The session-model check is argv-only,
# a known gap the verifiability matrix records.


def _pooled_graph_yaml() -> str:
    """The same build -> review -> deliver shape, expressed with a named pool.

    `build` is `fresh:code`, minting each entry; `review` is `resume:code`, continuing the
    head `build` just minted — a pairing no `resume:<node>` form could express, since
    `review` has never run at its own node when it first resumes."""
    import yaml

    graph = _yaml_graph_dict()
    # Must stay the hub's configured default-graph name — ingest pins a fresh chunk to
    # the default by name; a differently-named graph mints but is silently never used.
    graph["sessions"] = {"code": {"model": ["blizzard:basic"], "effort": "medium"}}
    graph["nodes"]["build"]["session"] = "fresh:code"
    graph["nodes"]["review"]["session"] = "resume:code"
    return yaml.safe_dump(graph, sort_keys=False)


def _yaml_graph_dict() -> dict:
    """The graph dict `_graph_yaml` builds, re-parsed — one owner for the shape."""
    import yaml

    return yaml.safe_load(_graph_yaml())


def _session_invocations(workspace: Path, session_id: str) -> list[dict]:
    """The mock's recorded per-turn `--model`/`--effort` flags for ``session_id``."""
    state_path = workspace / ".blizzard-mock-harness" / "sessions" / f"{session_id}.json"
    assert state_path.is_file(), f"no persisted mock session state at {state_path}"
    return list(json.loads(state_path.read_text())["invocations"])


def test_a_named_pool_threads_one_session_across_nodes_and_reasserts_its_model(tmp_path: Path) -> None:
    """`fresh:code` mints; `resume:code` at a *different* node continues it; the mint
    and every resume carry the same resolved model."""
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")

    scratch = tmp_path / "scratch"
    mint_world(bin_dir, scratch)
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"

    forge_port, hub_port = free_port(), free_port()
    runner_dir = tmp_path / "runner"
    db_url = ""
    with (
        run_forge(bin_dir, fixture_root / "origins", forge_port) as forge,
        run_hub(tmp_path / "hub", forge_port, hub_port) as hub,
    ):
        assert hub.post("/api/graphs", json={"definition_yaml": _pooled_graph_yaml()}).status_code == 201

        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "pooled sessions", "body": "the chunk"})
        assert issue.status_code == 201, issue.text
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        config = runner_config(runner_dir, workspace, bin_dir, hub_port)
        db_url = config.db_url
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        status = drive_until_done(config, hub, chunk_id, fenced)

        assert status == "done", f"chunk did not reach done (last status {status!r})"

    by_node = _sessions_by_node(db_url, chunk_id)
    build_sessions, review_sessions = by_node.get("build", []), by_node.get("review", [])
    assert len(build_sessions) == 2, f"build should have been entered twice: {by_node}"
    assert len(review_sessions) == 2, f"review should have been entered twice: {by_node}"

    # `fresh:code` is a forced rotation point — each build entry MINTS a head.
    assert len(set(build_sessions)) == 2, f"fresh:code did not mint per entry: {build_sessions}"
    # …and each `resume:code` review continued the head its own iteration just minted —
    # a session `review` has never run at its own node, so `resume:<node>` could not do it.
    assert review_sessions[0] == build_sessions[0], f"review did not continue build's head: {by_node}"
    assert review_sessions[1] == build_sessions[1], f"review did not continue the NEW head: {by_node}"

    # The pool never forked: one head at a time across the whole traversal.
    pool_order = [session for node in ("build", "review") for session in by_node.get(node, [])]
    assert set(pool_order) == set(build_sessions), f"a session appeared outside the pool's heads: {by_node}"

    # The session-model contract, off the mock's own recorded argv. Note this asserts
    # the FLAG, not the effective model — the facade sees argv and nothing else.
    for head in set(build_sessions):
        invocations = _session_invocations(workspace, head)
        mints = [i for i in invocations if i["kind"] == "spawn"]
        resumes = [i for i in invocations if i["kind"] == "resume"]
        assert mints, f"session {head} recorded no mint: {invocations}"
        assert all(i["model"] == "sonnet" for i in mints), f"the basic pool did not mint on sonnet: {mints}"
        assert resumes, f"session {head} recorded no resume — the contract would pass vacuously"
        assert all(i["model"] == mints[0]["model"] for i in resumes), f"a resume changed the session model: {resumes}"
        # Effort IS reasserted on every turn — it is not session-sticky.
        assert all(i["effort"] == "medium" for i in invocations), f"effort was not reasserted: {invocations}"
