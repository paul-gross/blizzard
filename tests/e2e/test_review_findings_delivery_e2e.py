"""The `record-findings` node end to end (blizzard#582 Phase 3): the real packaged
`basic-development-workflow` graph, its `deliver`/`record-findings` scripts unscripted, runs
against the real hub route. A scripted review publishes a `review-finding-delta` with one
`deferred`, one `fixed`, and one `refuted` entry; after landing, only the deferred entry is a
durable review-sourced finding, readable through `GET /api/findings`."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from blizzard.hub.graphs import PACKAGED
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
        reason="e2e review findings delivery needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_SCOPE = "e2e-review-findings"

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

# One deferred (mints a fin_ row), one fixed, one refuted (mints nothing).
_DELTA = json.dumps(
    {
        "entries": [
            {
                "ref": "F1",
                "disposition": "deferred",
                "severity": "should-fix",
                "scope": _SCOPE,
                "class": "simplification",
                "locus": f"{REPO_NAME}/BUILD.md:1",
                "summary": "The changelog line could fold into the existing entry above it.",
            },
            {"ref": "F2", "disposition": "fixed"},
            {"ref": "F3", "disposition": "refuted"},
        ]
    }
)
_REVIEW_SCRIPT = (
    "import subprocess\n"
    "subprocess.run(\n"
    "    ['blizzard', 'runner', 'artifact', 'create', '--name', 'review-finding-delta'],\n"
    f"    input={_DELTA!r}, text=True, check=True,\n"
    ")\n"
)
_REVIEW_JUDGEMENT = "verdict('pass', 'cold-eyes review: one should-fix deferred, one fixed, one refuted')\n"

_PRE_PUSH_SCRIPT = f"import subprocess\nrepo = {REPO_NAME!r}\n" + _PUSH_AND_DECLARE_SCRIPT
_PRE_PUSH_JUDGEMENT = "verdict('clean', 'no conflicts; fast-forward ready')\n"

_RETROSPECTIVE_SCRIPT = "pass\n"
_RETROSPECTIVE_JUDGEMENT = "verdict('recorded', 'landed clean; the review round left one deferred finding')\n"


def _scripted_graph_yaml() -> str:
    """The real packaged `basic-development-workflow` body with only its runner-node
    prompts swapped for scripts — `deliver` and `record-findings` (both hub-executor
    script nodes) travel verbatim, so this exercises the real `land_ff`/`review_deliver`
    scripts and the real hub route/domain code, not a mock of either."""
    body: Any = PACKAGED.named("basic-development-workflow").body
    # The built-in triage router's own migration-target name, so a freshly ingested
    # chunk mints straight onto it with no unscripted triage node in the loop.
    body["name"] = "default-delivery"
    nodes: Any = body["nodes"]
    nodes["build"]["prompt"] = _BUILD_SCRIPT
    nodes["build"]["judgement"]["prompt"] = _BUILD_JUDGEMENT
    nodes["review"]["prompt"] = _REVIEW_SCRIPT
    nodes["review"]["judgement"]["prompt"] = _REVIEW_JUDGEMENT
    nodes["pre-push"]["prompt"] = _PRE_PUSH_SCRIPT
    nodes["pre-push"]["judgement"]["prompt"] = _PRE_PUSH_JUDGEMENT
    nodes["retrospective"]["prompt"] = _RETROSPECTIVE_SCRIPT
    nodes["retrospective"]["judgement"]["prompt"] = _RETROSPECTIVE_JUDGEMENT
    return yaml.safe_dump(body, sort_keys=False)


def test_review_finding_delta_mints_exactly_its_deferred_entry_at_landing(tmp_path: Path) -> None:
    """`review` publishes a delta of one deferred, one fixed, one refuted entry;
    `deliver` lands the branch; `record-findings` materializes exactly the deferred
    entry as a durable review-sourced finding, readable via `GET /api/findings`."""
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
        assert hub.post("/api/graphs", json={"definition_yaml": _scripted_graph_yaml()}).status_code == 201

        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "review findings delivery", "body": "the chunk"})
        assert issue.status_code == 201, issue.text
        issue_number = issue.json()["number"]
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue_number}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        status = _drive_until_done(config, hub, chunk_id, fenced)

        assert status == "done", f"chunk did not reach done (last status {status!r})"

        # record-findings ran between deliver and retrospective — a visible step.
        history = hub.get(f"/api/chunks/{chunk_id}").json()["history"]
        assert any(h["choice_name"] == "recorded" and h["from_node_name"] == "record-findings" for h in history), (
            f"no record-findings 'recorded' step in the chunk history: {history}"
        )

        findings = hub.get("/api/findings", params={"scope": _SCOPE, "source": "review"}).json()["findings"]
        assert len(findings) == 1, f"expected exactly one review-sourced finding, got: {findings}"
        finding = findings[0]
        assert finding["source"] == "review"
        assert finding["severity"] == "should-fix"
        assert finding["routine_name"] is None
        assert finding["raised_by_chunk_id"] == chunk_id
        assert finding["class"] == "simplification"
        assert finding["locus"] == f"{REPO_NAME}/BUILD.md:1"

    # land_ff fast-forwards the base ref directly (no PR) — the build commit is on
    # bare main.
    build_md = _git_bare(origin_bare, "show", "main:BUILD.md")
    assert "build pass" in build_md, f"the build commit did not land on main:\n{build_md}"
