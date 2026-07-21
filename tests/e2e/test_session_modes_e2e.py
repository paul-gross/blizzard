"""Node ``session:`` modes end to end — scenario 11 of the standing e2e smoke — issue #115.

The full-stack proof that a node's authored ``session:`` mode actually governs which
harness session a node-entry spawn continues, on the real forge + hub + runner +
``mock-claude-code`` rails — the one thing no existing scenario asserts: *session
continuity across a graph transition*. The sibling ``test_review_cycle_e2e`` drives the
same ``build -> review -> build`` fail-cycle shape but only asserts the work re-runs and
the findings thread back; it never checks *which session* each spawn ran under.

Here the graph carries the real feature's own modes — ``build`` is ``session: resume:build``
(the packaged default's setting, plan Q4) and ``review`` is ``session: fresh`` — and a
scripted review **fails once then passes**, so ``build`` is entered twice. The mock
harness persists each session's state (``turns``, keyed by ``session_id``, under
``<workspace>/.blizzard-mock-harness/sessions/``) and the runner store records the
``session_id`` of every node-step lease, so together they prove:

* **(a) re-entered build resumed its OWN prior build session** — both ``build`` leases
  carry the *same* ``session_id``, and that session's persisted ``turns`` grew past a
  single visit's worth (spawn + judgement resume), i.e. the second entry continued the
  first in place rather than spawning fresh;
* **(b) each ``fresh`` review ran on a NEW session** — the two ``review`` leases carry
  two *distinct* ``session_id``s, disjoint from build's;
* **(c) first arrival at build spawned fresh** — the chunk's very first lease can resume
  nothing, so build's shared session was minted fresh at first entry and *re-entered*,
  never re-spawned (the single-build-session cardinality is exactly this);
* **why the targeted form is load-bearing (plan Q4)** — the chunk's most-recent session
  overall (what bare ``resume`` would inherit) is the *reviewer's* fresh session, NOT
  build's; ``resume:build`` is what makes the re-entered build resume the right one.

Reuses the acceptance loop's live-stack scaffolding (forge/hub/runner harnesses, fixture
mint, port helpers) exactly like the sibling scenarios. Skipped unless ``BLIZZARD_E2E=1``
with the sibling ``blizzard-mock`` worktree provisioned.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from pathlib import Path

import pytest

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from tests.e2e.test_acceptance_loop import (
    FIXTURE_ENV,
    REPO,
    REPO_NAME,
    _drive_until_done,
    _forge,
    _free_port,
    _hub,
    _mock_bin_dir,
    _runner_config,
    _winter_source,
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e session modes needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# build: append a line to BUILD.md and commit — always a real change, so a re-entry after
# a review fail commits again (two commits => build ran twice, entered twice).
_BUILD_SCRIPT = (
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    'p = pathlib.Path(repo) / "BUILD.md"\n'
    'p.write_text((p.read_text() if p.exists() else "") + "build pass\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: a build pass"],\n'
    "    check=True,\n"
    ")\n"
)
_BUILD_JUDGEMENT = "verdict('pass', 'checks are green')\n"

# review base prompt: a no-op turn — the verdict is elicited on the judgement resume.
_REVIEW_SCRIPT = "pass\n"
# review judgement: fail the FIRST visit (a marker file in the held env dir persists
# across the cycle), pass the second — 'fails once then passes'.
_REVIEW_JUDGEMENT = (
    "import pathlib\n"
    "m = pathlib.Path('.review-count')\n"
    "n = (int(m.read_text()) if m.exists() else 0) + 1\n"
    "m.write_text(str(n))\n"
    "if n == 1:\n"
    "    verdict('fail', 'BLOCKING: guard the empty input before delivery')\n"
    "else:\n"
    "    verdict('pass', 'findings addressed; ready to deliver')\n"
)


def _graph_yaml() -> str:
    """A ``build -> review -> deliver`` graph carrying the real feature's session modes.

    ``build`` is ``session: resume:build`` (the packaged default's own setting) and
    ``review`` is ``session: fresh`` — the exact shape plan Q4 exists to express.
    """
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _BUILD_SCRIPT,
                # The feature under test: a re-entered build resumes its OWN prior build
                # session, not the reviewer's more-recent fresh one (plan Q4).
                "session": "resume:build",
                "judgement": {
                    "prompt": _BUILD_JUDGEMENT,
                    "choices": {
                        "pass": {"description": "Committed and green.", "to": "review"},
                        "fail": {"description": "Incomplete.", "to": "build"},
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "review": {
                "executor": "runner",
                "prompt": _REVIEW_SCRIPT,
                "session": "fresh",
                "produces": ["review-findings"],
                "judgement": {
                    "prompt": _REVIEW_JUDGEMENT,
                    "choices": {
                        "pass": {"description": "Passes review.", "to": "deliver"},
                        "fail": {"description": "Blocking issues found.", "to": "build"},
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "run": [{"command": "python3 -m blizzard.hub.graphs.scripts.land_default"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Landed.", "to": "done"},
                        "conflict": {"description": "Conflict.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _sessions_by_node(db_url: str, chunk_id: str) -> dict[str, list[str]]:
    """Map ``node_name -> [session_id, ...]`` (mint order) from the runner store.

    Reopens the runner's own sqlite store after the run — the durable record of which
    session each node-step lease actually spawned/continued under, the runner-side truth
    that pairs with the mock's on-disk per-session ``turns``.
    """
    store = SqlAlchemyRunnerStore(create_engine_from_url(db_url))
    leases = [store.lease(lid) for lid in store.lease_ids_for_chunk(chunk_id)]
    ordered = sorted((lz for lz in leases if lz is not None), key=lambda lz: lz.created_at)
    by_node: dict[str, list[str]] = {}
    for lz in ordered:
        if lz.session_id is not None:
            by_node.setdefault(lz.node_name, []).append(lz.session_id)
    return by_node


def _session_turns(workspace: Path, session_id: str) -> int:
    """The mock's persisted ``turns`` for ``session_id`` (spawn + each resume)."""
    state_path = workspace / ".blizzard-mock-harness" / "sessions" / f"{session_id}.json"
    assert state_path.is_file(), f"no persisted mock session state at {state_path}"
    return int(json.loads(state_path.read_text())["turns"])


def test_session_modes_resume_targeted_and_fresh_across_a_cycle(tmp_path: Path) -> None:
    """build (`resume:build`) re-entry resumes its own session; `fresh` review is new each time."""
    bin_dir = _mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    winter_source = _winter_source()
    if winter_source is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")

    scratch = tmp_path / "scratch"
    subprocess.run(
        [
            str(bin_dir / "blizzard-mock-fixture"),
            "reset",
            "--env",
            FIXTURE_ENV,
            "--scratch-root",
            str(scratch),
            "--winter-source",
            str(winter_source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"
    origin_bare = origins / f"{REPO_NAME}.git"
    (workspace / ".blizzard-mock-harness-fence").write_text("e2e fence marker\n")

    forge_port, hub_port = _free_port(), _free_port()
    runner_dir = tmp_path / "runner"
    db_url = ""
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201

        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "session modes", "body": "the chunk"})
        assert issue.status_code == 201, issue.text
        issue_number = issue.json()["number"]
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue_number}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202  # ready for the runner

        config = _runner_config(runner_dir, workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, max_agents=1)
        db_url = config.db_url
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        status = _drive_until_done(config, hub, chunk_id, fenced)

        assert status == "done", f"chunk did not reach done (last status {status!r})"

    # The review-fail cycle ran build TWICE — two 'build pass' lines land on main. This is
    # the same observable the sibling review-cycle scenario asserts, and here it is the
    # precondition for the session assertions: build was genuinely entered twice.
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
    # the first's fresh session in place rather than spawning a new one. A regression that
    # dropped `resume:build` (or failed to thread `session_source` through the envelope)
    # would re-spawn fresh here and yield two distinct build session ids.
    assert len(set(build_sessions)) == 1, (
        f"re-entered build did not resume its own session — got distinct ids {build_sessions}"
    )
    build_session = build_sessions[0]

    # (b): the two `fresh` review visits ran on two DISTINCT sessions, disjoint from build's.
    assert len(set(review_sessions)) == 2, f"fresh review did not get a new session each visit: {review_sessions}"
    assert set(review_sessions).isdisjoint(build_sessions), (
        f"a review session collided with build's: reviews={review_sessions} build={build_session}"
    )

    # The mock-persisted turn count proves build's session was CONTINUED, not merely
    # reused as a label: a single build visit is spawn + judgement resume (2 turns); two
    # entries continuing the same session accumulate strictly more. Each fresh review, by
    # contrast, is its own single-visit session.
    assert _session_turns(workspace, build_session) > 2, (
        f"build session {build_session} did not accumulate turns across two entries "
        f"(turns={_session_turns(workspace, build_session)}) — it was not resumed in place"
    )
    for review_session in set(review_sessions):
        assert _session_turns(workspace, review_session) >= 1, f"review session {review_session} has no persisted turns"

    # Why the TARGETED form is load-bearing (plan Q4): the chunk's most-recent session
    # overall — what a bare `resume` would inherit — is the reviewer's fresh session, NOT
    # build's own. `resume:build` is exactly what avoids that wrong inheritance.
    store = SqlAlchemyRunnerStore(create_engine_from_url(db_url))
    assert store.latest_session_id(chunk_id, "build") == build_session
    chunk_most_recent = store.latest_session_id(chunk_id, None)
    assert chunk_most_recent in review_sessions, (
        f"the chunk's most-recent session should be a review one, got {chunk_most_recent!r}"
    )
    assert chunk_most_recent != build_session, (
        "targeted resume:build must differ from bare resume here — else the scenario proves nothing"
    )
