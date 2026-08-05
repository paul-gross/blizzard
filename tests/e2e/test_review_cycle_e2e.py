"""The review-fail cycle end to end — scenario 2 of the standing e2e smoke — MVP criterion 9.

The happy-path `build -> review -> deliver` companion, but the scripted review fails once
before passing: the `fail` edge carries its findings + `prompt_addendum` into build's
re-entry, and build runs twice — both commits land on bare `main`.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path

import pytest

from tests.e2e.test_acceptance_loop import (
    _PUSH_AND_DECLARE_SCRIPT,
    FIXTURE_ENV,
    REPO,
    REPO_NAME,
    _drive_until_done,
    _forge,
    _free_port,
    _git_bare,
    _hub,
    _mock_bin_dir,
    _runner_config,
    _winter_source,
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e review cycle needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# build: append a line to BUILD.md and commit — always a real change, so a re-entry
# after a review fail commits again (two commits => build ran twice).
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
    ")\n" + _PUSH_AND_DECLARE_SCRIPT
)
_BUILD_JUDGEMENT = "verdict('pass', 'checks are green')\n"

# Inlined onto build's re-entry prompt, sharing its namespace (`repo`, `subprocess`,
# `pathlib`); REVIEW_ADDRESSED.md lands only if the addendum threaded into re-entry.
_REVIEW_ADDENDUM = (
    "# re-entry after a failed review — address the findings\n"
    'pathlib.Path(repo, "REVIEW_ADDRESSED.md").write_text("addressed the review findings\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "fix: address review findings"],\n'
    "    check=True,\n"
    ")\n" + _PUSH_AND_DECLARE_SCRIPT
)

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
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _BUILD_SCRIPT,
                "judgement": {
                    "prompt": _BUILD_JUDGEMENT,
                    "choices": {
                        "pass": {"description": "Committed and green.", "to": "review"},
                        "fail": {
                            "description": "Incomplete.",
                            "to": "build",
                            "prompt_addendum": "# address the findings\n",
                        },
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
                        "fail": {
                            "description": "Blocking issues found.",
                            "to": "build",
                            "prompt_addendum": _REVIEW_ADDENDUM,
                        },
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


def test_review_cycle_fails_once_then_delivers(tmp_path: Path) -> None:
    """A scripted review fails once, the work re-builds, review passes, delivery lands."""
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
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201

        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "review cycle", "body": "the chunk"})
        assert issue.status_code == 201, issue.text
        issue_number = issue.json()["number"]
        ingested = hub.post(
            "/api/chunks",
            json={"tokens": [f"{REPO_NAME}:{issue_number}"]},
        )
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202  # ready for the runner

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, max_agents=1)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        status = _drive_until_done(config, hub, chunk_id, fenced)

        assert status == "done", f"chunk did not reach done (last status {status!r})"
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert any(p.get("merged") for p in pulls), f"no PR merged at the forge: {pulls}"

        # The chunk detail is where the cycle has to remain visible after the fact: the
        # full transition history, and the review-findings asset content.
        detail = hub.get(f"/api/chunks/{chunk_id}").json()
        history = detail["history"]
        # The review judged fail once, routing review -> build: a visible step in the timeline.
        fail_steps = [h for h in history if h["choice_name"] == "fail"]
        assert fail_steps, f"no review-fail step in the chunk history: {history}"
        assert any(h["choice_name"] == "pass" for h in history), f"no passing step in the history: {history}"
        # The review's findings asset — the failing visit's assessment — is inline on the
        # detail, its content the verdict reason the fail carried back.
        findings = [a for a in detail["artifacts"] if a["name"] == "review-findings" and a["kind"] == "asset"]
        assert findings, f"no review-findings asset on the chunk detail: {detail['artifacts']}"
        assert any("BLOCKING" in (a["content"] or "") for a in findings), (
            f"the fail visit's findings content is not exposed: {[a['content'] for a in findings]}"
        )

    # The review-fail cycle ran build TWICE — two 'build pass' lines land on main.
    build_md = _git_bare(origin_bare, "show", "main:BUILD.md")
    assert build_md.count("build pass") == 2, f"expected two build passes on main, got:\n{build_md}"

    # The addendum's committed marker is reachable from main only if it arrived as code
    # the re-entry build ran.
    tree = _git_bare(origin_bare, "ls-tree", "-r", "--name-only", "main")
    assert "REVIEW_ADDRESSED.md" in tree.split(), f"re-entry addendum did not land on main:\n{tree}"
