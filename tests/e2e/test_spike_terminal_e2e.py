"""A non-code chunk completes with only asset artifacts — MVP criterion 10 (2nd sentence).

The sibling e2e scenarios all end in a git deliver that lands **code**: their chunks
build a change, so the deliver node merges branch pointers onto bare ``main``. This
scenario covers the other half of criterion 10 — *"A chunk whose purpose is non-code
work (a review, a spike) completes with only asset artifacts"* — end to end on the real
rails. Per the engine design (``blizzard-discovery:/design/workflow-engine.md``): *"A
chunk whose whole purpose is a review or a spike simply ends with assets instead of
branch pointers"*, and the graph still *"ends in a deliver node"* — the uniform terminal.

One chunk travels a **spike** node whose worker does read-only investigation (no commit,
so nothing is pushed and ``_push_and_collect_artifacts`` yields no git-commit artifact)
and ``produces`` a ``spike-notes`` **asset**, whose content is the worker's judgement
assessment — the text after ``</Choice>``. The spike routes into the hub
**deliver** node exactly as a code chunk does; because the chunk carries *no* git-commit
pointers, the merge-queue coordinator lands nothing — it opens no PR and moves no ``main``
— yet still writes the terminal ``delivery.landed`` fact, so the chunk derives ``done``
carrying only its asset (``hub/delivery/coordinator.py``: an empty ``pointers`` list
skips straight to ``_landed``). The assertions pin the criterion at all three truths:

* **fleet truth** — the hub derives the chunk ``done`` (an empty delivery still lands);
* **hub-durable artifacts** — the chunk detail exposes exactly one artifact, the
  ``spike-notes`` asset, and **zero** ``git_commit`` artifacts;
* **git truth** — no PR is opened at the forge and bare ``main`` is untouched: a non-code
  chunk lands no code.

This is the terminal-with-only-assets case no other e2e reaches: the review-cycle asset
is a *fail-loop intermediate* on a chunk that still delivers code, whereas here the asset
is the chunk's whole, terminal output. Reuses the acceptance loop's live-stack scaffolding
(forge/hub/runner harnesses, fixture mint, port helpers); skipped unless ``BLIZZARD_E2E=1``
with the sibling ``blizzard-mock`` worktree provisioned, exactly like test_acceptance_loop.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.e2e.test_acceptance_loop import (
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
        reason="e2e spike terminal needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# The spike's base turn: a read-only investigation — no file written, no commit made — so
# the runner discovers no produced branch and pushes nothing (the non-code path). A bare
# ``pass`` is the mock's no-op turn; the finding is elicited on the judgement resume.
_SPIKE_SCRIPT = "pass\n"
# The spike's judgement: route to the deliver node (``complete -> deliver``) and carry the
# investigation write-up as the assessment. The text after ``</Choice>`` becomes the
# ``spike-notes`` asset's content — the chunk's whole, terminal output.
_SPIKE_NOTES = "SPIKE: investigated toy-api; the change is not warranted — no code needed."
_SPIKE_JUDGEMENT = f"verdict('complete', {_SPIKE_NOTES!r})\n"


def _graph_yaml() -> str:
    """A ``default-delivery`` graph whose only work node is an asset-producing spike.

    Named ``default-delivery`` so the hub's lazy ``ensure_default`` (POST /chunks) pins
    this pre-minted graph by name instead of the packaged prose default. The
    spike ``produces`` a ``spike-notes`` asset and routes into the same hub ``deliver``
    node a code chunk uses (workflow-engine.md: the graph ends in a deliver node). With
    no branch pointers to land, the deliver is an empty land: no PR, no ``main`` move,
    but a terminal ``delivery.landed`` fact — so the chunk reaches ``done`` carrying only
    its produced asset.
    """
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "spike",
        "nodes": {
            "spike": {
                "executor": "runner",
                "prompt": _SPIKE_SCRIPT,
                "produces": ["spike-notes"],
                "judgement": {
                    "prompt": _SPIKE_JUDGEMENT,
                    "choices": {
                        "complete": {
                            "description": "The investigation is complete; findings recorded.",
                            "to": "deliver",
                        }
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {"executor": "hub", "mode": "merge-to-main"},
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def test_spike_chunk_terminates_with_only_asset_artifacts(tmp_path: Path) -> None:
    """A non-code chunk reaches ``done`` carrying only its asset — no code lands (criterion 10)."""
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

    # The bare origin's pre-run tip: git truth that a non-code chunk moves nothing.
    main_before = _git_bare(origin_bare, "rev-parse", "main").strip()

    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201

        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "spike toy-api", "body": "investigate only"})
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
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        status = _drive_until_done(config, hub, chunk_id, fenced)

        # Fleet truth: the chunk reached the terminal (the empty deliver still lands).
        assert status == "done", f"spike chunk did not reach done (last status {status!r})"

        # Hub-durable artifacts: exactly the spike-notes asset, and no git commit.
        detail = hub.get(f"/api/chunks/{chunk_id}").json()
        artifacts = detail["artifacts"]
        assert [a["kind"] for a in artifacts] == ["asset"], f"expected a single asset artifact, got: {artifacts}"
        note = artifacts[0]
        assert note["name"] == "spike-notes"
        assert note["content"] == _SPIKE_NOTES, f"asset content is not the worker's write-up: {note['content']!r}"
        assert not [a for a in artifacts if a["kind"] == "git_commit"], f"a non-code chunk pushed a commit: {artifacts}"

        # Git truth: nothing was delivered — no PR opened at the forge.
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert pulls == [], f"a non-code chunk opened a PR at the forge: {pulls}"

    # Git truth: bare main is exactly where it started — a non-code chunk lands no code.
    main_after = _git_bare(origin_bare, "rev-parse", "main").strip()
    assert main_after == main_before, f"bare main moved for a non-code chunk: {main_before} -> {main_after}"
