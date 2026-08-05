"""Checks-gate enforcement end to end — issue #114, the full-stack proof.

Two scenarios over the real forge + hub + runner: a red gated ``pass``
(``requires_checks: true``) is bounced and re-queued, landing once green (AC #4); a red
check reported through a non-gated ``fail`` routes normally (AC #5). Reuses the
acceptance loop's live-stack scaffolding; skipped unless ``BLIZZARD_E2E=1``."""

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
        reason="e2e checks gate needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_LAND = "python3 -m blizzard.hub.graphs.scripts.land_default"

# Bumps a persistent `.build-count`, creates `.checks-ok` on the second+ visit (flipping
# the check red->green), and always commits so "build ran twice" is provable on bare main.
_BUILD_SCRIPT = (
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    "c = pathlib.Path('.build-count')\n"
    "n = (int(c.read_text()) if c.exists() else 0) + 1\n"
    "c.write_text(str(n))\n"
    "if n >= 2:\n"
    "    pathlib.Path('.checks-ok').write_text('ok')\n"
    "p = pathlib.Path(repo) / 'CHECKS.md'\n"
    "p.write_text((p.read_text() if p.exists() else '') + f'build {n}\\n')\n"
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: a build pass"],\n'
    "    check=True,\n"
    ")\n" + _PUSH_AND_DECLARE_SCRIPT
)

# Always-pass judgement: the gate — not the worker — is what bounces the red attempt.
_ALWAYS_PASS = "verdict('pass', 'submitting for the gate to judge against the checks')\n"

# Non-gated-fail judgement: select the ungated `fail` on the first (red) visit, `pass` on
# the second (green). Reads the same `.build-count` marker the build turn bumped.
_FAIL_THEN_PASS = (
    "import pathlib\n"
    "n = int(pathlib.Path('.build-count').read_text())\n"
    "if n == 1:\n"
    "    verdict('fail', 'a check is red; routing back through the non-gated fail')\n"
    "else:\n"
    "    verdict('pass', 'checks are green now')\n"
)


def _graph_yaml(*, build_judgement: str) -> str:
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _BUILD_SCRIPT,
                "checks": ["test -f .checks-ok"],
                "judgement": {
                    "prompt": build_judgement,
                    "choices": {
                        "pass": {"description": "Committed and green.", "to": "deliver", "requires_checks": True},
                        "fail": {"description": "Not ready.", "to": "build"},
                    },
                },
                "retries": {"max": 3, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "run": [{"command": _LAND}],
                "judgement": {
                    "choices": {
                        "success": {"description": "Delivered.", "to": "done"},
                        "failure": {"description": "Failed to deliver.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _drive(tmp_path: Path, graph_yaml: str, title: str):
    """Mint the fixture, stand up forge+hub, ingest+drive one chunk to done. Returns the
    chunk detail and the bare origin path for git-truth assertions."""
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
    origin_bare = fixture_root / "origins" / f"{REPO_NAME}.git"
    (workspace / ".blizzard-mock-harness-fence").write_text("e2e fence marker\n")

    forge_port, hub_port = _free_port(), _free_port()
    with (
        _forge(bin_dir, fixture_root / "origins", forge_port) as forge,
        _hub(tmp_path / "hub", forge_port, hub_port) as hub,
    ):
        assert hub.post("/api/graphs", json={"definition_yaml": graph_yaml}).status_code == 201
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": title, "body": "the chunk"})
        assert issue.status_code == 201, issue.text
        chunk_id = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]}).json()[
            "chunk_id"
        ]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        config = dataclasses.replace(_runner_config(tmp_path / "runner", workspace, bin_dir, hub_port), max_agents=1)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        status = _drive_until_done(config, hub, chunk_id, fenced)

        assert status == "done", f"chunk did not reach done (last status {status!r})"
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert any(p.get("merged") for p in pulls), f"no PR merged at the forge: {pulls}"
        detail = hub.get(f"/api/chunks/{chunk_id}").json()
    return detail, origin_bare


def test_checks_gate_bounces_a_red_pass_then_lands_when_green(tmp_path: Path) -> None:
    """The gated ``pass`` is bounced while the check is red and lands once it is green — build
    ran twice, only the green attempt delivered (AC #4)."""
    _detail, origin_bare = _drive(tmp_path, _graph_yaml(build_judgement=_ALWAYS_PASS), "checks gate bounce")

    # Build ran twice: the git-truth proof the first (red) `pass` was bounced and re-run
    # (a gate bounce leaves no history transition row).
    checks_md = _git_bare(origin_bare, "show", "main:CHECKS.md")
    assert checks_md.count("build ") == 2, f"expected two build passes on main (a bounce + a land), got:\n{checks_md}"


def test_a_red_check_through_a_non_gated_fail_routes_normally(tmp_path: Path) -> None:
    """A red check reported through the ungated ``fail`` choice is an ordinary judged
    transition back to build — the gate never fires — and the green re-entry lands (AC #5)."""
    detail, origin_bare = _drive(tmp_path, _graph_yaml(build_judgement=_FAIL_THEN_PASS), "checks gate non-gated fail")

    # The non-gated `fail` routed normally: a visible fail step in the chunk history (a gate
    # bounce would leave none — it is a failure, not a judged transition).
    history = detail["history"]
    assert any(h["choice_name"] == "fail" for h in history), f"no non-gated fail step in the history: {history}"
    assert any(h["choice_name"] == "pass" for h in history), f"no landing pass step in the history: {history}"
    checks_md = _git_bare(origin_bare, "show", "main:CHECKS.md")
    assert checks_md.count("build ") == 2, f"expected two build passes on main, got:\n{checks_md}"
