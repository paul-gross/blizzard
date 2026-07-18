"""Retries exhausted -> needs_human -> resumable takeover — scenario 3 of the standing
e2e smoke — MVP criterion 6.

The third full-stack scenario alongside the happy path (test_acceptance_loop) and the
review-fail cycle (test_review_cycle_e2e). One chunk is driven through the real forge +
hub + runner + ``mock-claude-code`` façade where the build worker **exits without a
parseable verdict twice** — a clean exit whose judgement resume yields no
``<Choice>``. With ``retries.max = 1`` that is two failed execution attempts,
so the node's budget is exhausted and the runner **escalates**:

* the chunk derives **needs_human** — an open ``escalation.recorded`` with no later
  lease mint, flushed up the runner's outbound buffer to
  ``POST /api/fleet/events`` and dispatched to ``record_escalation``;
* the open escalation surfaces the runner-composed **takeover command** — the
  pasteable ``cd <workdir> && <harness> --resume <session>``;
* and that command, **executed verbatim in the env**, actually resumes the parked
  mock session — asserted by the session's own persisted state advancing (a new turn
  recording the human's takeover message), not merely by the string existing.

Reuses the acceptance loop's live-stack scaffolding. Skipped unless ``BLIZZARD_E2E=1``
with the sibling ``blizzard-mock`` worktree provisioned — exactly like the siblings.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

import pytest

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
        reason="e2e escalation needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# build: a clean no-op turn — the worker exits successfully but does no work of note.
_BUILD_SCRIPT = "pass\n"
# build judgement: a no-op that emits NO verdict() — so the resume reply carries no
# ``<Choice>``, which the adapter parses to None and the core treats as a failed
# attempt. Every attempt fails this way, so the node's retry budget exhausts.
_VERDICTLESS_JUDGEMENT = "pass\n"

# The takeover message a human types into the resumed session. It arrives as code (a
# comment is valid, no-op Python) and is recorded verbatim in the session's `resumes`
# before it runs — so its presence there proves the verbatim command resumed THIS
# session. Unique per run so a stale session file can never satisfy the assertion.
_TAKEOVER_MARKER = f"human-takeover-{uuid.uuid4().hex}"
_TAKEOVER_INPUT = f"# {_TAKEOVER_MARKER}\n"


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
                    "prompt": _VERDICTLESS_JUDGEMENT,
                    "choices": {
                        # A valid target keeps the graph well-formed; the worker never
                        # reaches it — it exits verdict-less every attempt.
                        "pass": {"description": "Committed and green.", "to": "deliver"},
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


def _session_state_path(workspace: Path, session_id: str) -> Path:
    """Where the mock harness persists the session keyed by ``session_id``.

    Defaults beside the fence marker (blizzard_mock.harness.engine._state_root): the
    marker sits at the workspace root, so every acquired env worktree under it shares
    ``<workspace>/.blizzard-mock-harness/sessions``.
    """
    return workspace / ".blizzard-mock-harness" / "sessions" / f"{session_id}.json"


def test_retries_exhausted_escalates_and_takeover_resumes_session(tmp_path: Path) -> None:
    """Two verdict-less exits exhaust the budget; the escalation's takeover command,
    run verbatim, resumes the parked mock session."""
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
    (workspace / ".blizzard-mock-harness-fence").write_text("e2e fence marker\n")

    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201

        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "escalation", "body": "the chunk"})
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

        # The retry budget exhausted after two verdict-less attempts -> needs_human.
        assert status == "needs_human", f"chunk did not derive needs_human (last status {status!r})"

        detail = hub.get(f"/api/chunks/{chunk_id}").json()
        assert detail["status"] == "needs_human"
        escalation = detail["escalation"]
        assert escalation is not None, "an open escalation should surface on a needs_human chunk"
        takeover = escalation["takeover_command"]
        assert takeover, "the escalation must surface a pasteable takeover command"

        # The parked session the takeover command targets (parsed from the command
        # itself — proof the command names a real, resumable session).
        match = re.search(r"--resume (\S+)", takeover)
        assert match is not None, f"takeover command carries no --resume session: {takeover!r}"
        session_id = match.group(1)
        state_path = _session_state_path(workspace, session_id)
        assert state_path.is_file(), f"the parked session state should exist on disk: {state_path}"
        turns_before = json.loads(state_path.read_text())["turns"]

        # Execute the takeover command VERBATIM (a human pasting it), feeding the
        # takeover message on stdin as the interactive resume would. The env carries the
        # mock fence, exactly as a provisioned feature env does.
        result = subprocess.run(
            takeover,
            shell=True,
            input=_TAKEOVER_INPUT,
            text=True,
            capture_output=True,
            env={**os.environ, "BLIZZARD_MOCK_HARNESS_FENCE": "1"},
        )
        assert result.returncode == 0, f"takeover command failed ({result.returncode}):\n{result.stderr}"

    # The verbatim command actually resumed the session: its persisted state advanced a
    # turn AND recorded the human's takeover message — not just that the string existed.
    after = json.loads(state_path.read_text())
    assert after["turns"] == turns_before + 1, f"takeover did not advance the session turn: {after}"
    assert any(_TAKEOVER_MARKER in resume for resume in after["resumes"]), (
        f"the resumed session did not record the takeover message: {after['resumes']}"
    )
