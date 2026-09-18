"""The OpenCode binding, service tier: the real runner against the mock hub, running its
worker turns under ``mock-opencode``. ``build`` mints fresh and resumes for judgement;
``review`` then shares that SAME session pool and resumes the identical session twice
more — the cross-node resume every harness's session pools give alike."""

from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop.build import LoopWiring
from blizzard.runner.store.schema import leases, usage_facts
from tests.e2e.test_acceptance_loop import REPO_NAME, _free_port, _git_bare, _runner_api, _runner_config
from tests.service.support import (
    JUDGEMENT_SCRIPT,
    mint_fixture,
    mock_hub,
    mock_hub_opencode_chunk_spec,
    poll_until,
    require_mock_fleet,
    require_winter_source,
    service_gate,
)

pytestmark = [pytest.mark.service, service_gate]

_WORK_REF_URL = "blizzard/toy-api/issues/1"


def _tick_env() -> dict[str, str]:
    fenced = dict(os.environ)
    fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
    return fenced


def _drive(config: RunnerConfig, fenced: dict[str, str], *, ticks: int, pause: float = 0.5) -> None:
    prior = dict(os.environ)
    os.environ.update(fenced)
    try:
        for _ in range(ticks):
            LoopWiring.of(config).tick_once()
            time.sleep(pause)
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _status(hub: httpx.Client, chunk_id: str) -> str:
    return hub.get(f"/api/fleet/chunks/{chunk_id}").json()["status"]


def _run_and_check(config: RunnerConfig, fenced: dict[str, str], hub: httpx.Client, chunk_id: str, target: str) -> bool:
    _drive(config, fenced, ticks=1, pause=0.3)
    return _status(hub, chunk_id) == target


def _seed_opencode(hub: httpx.Client) -> str:
    resp = hub.post("/_seed/chunk", json=mock_hub_opencode_chunk_spec(_WORK_REF_URL))
    assert resp.status_code == 201, resp.text
    return resp.json()["chunk_id"]


def _opencode_session_files(workspace: Path) -> list[Path]:
    """Every ``mock-opencode`` session state file this run minted — the mock's own on-disk
    record, keyed by the server-assigned session id no caller ever supplied."""
    return sorted((workspace / ".blizzard-mock-harness" / "sessions").glob("*.json"))


def test_opencode_build_and_review_resume_the_same_session_to_done(tmp_path: Path) -> None:
    """``build`` runs to completion on OpenCode, then ``review`` resumes that identical
    session rather than minting its own — asserted off the one session file
    ``mock-opencode`` ever wrote: spawn, then three resumes, in order."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        chunk_id = _seed_opencode(hub)
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
        assert landed, f"chunk did not land under OpenCode (status {_status(hub, chunk_id)!r})"

    # Exactly one OpenCode session was ever minted — `build` and `review` shared it, never
    # forking a second one, which is the whole point of the shared session-name pool.
    sessions = _opencode_session_files(workspace)
    assert len(sessions) == 1, f"expected exactly one OpenCode session, found {[p.name for p in sessions]}"
    state = json.loads(sessions[0].read_text())

    kinds = [invocation["kind"] for invocation in state["invocations"]]
    assert kinds == ["spawn", "resume", "resume", "resume"], (
        "build's fresh mint, build's judgement resume, review's node-entry resume, and "
        f"review's judgement resume, in order — got {kinds}"
    )


#: A real unified diff, applied by the mock's own ``git apply`` plumbing. Every
#: ``subprocess.run`` captures its own output (``capture_output=True``) — under OpenCode
#: the mock reads the first stdout line as the fresh-session identity, so a build script
#: must never let a child process's stdout reach the real one unfiltered.
_OPENCODE_TRANSCRIPT_DIFF = (
    "diff --git a/transcript-proof.txt b/transcript-proof.txt\n"
    "new file mode 100644\n"
    "index 0000000..1111111\n"
    "--- /dev/null\n"
    "+++ b/transcript-proof.txt\n"
    "@@ -0,0 +1 @@\n"
    "+minted by the real mock-opencode transcript writer\n"
)

_OPENCODE_TRANSCRIPT_COMMIT_MESSAGE = "feat: mint a transcript-provable commit under OpenCode"

#: Calls the mock's own ``apply_diff``/``commit`` helpers (not raw subprocess) so the run
#: mints matched ``Edit``/``Bash`` tool turns; repoints ``cwd`` to the repo child dir first.
_OPENCODE_TRANSCRIPT_BUILD_SCRIPT = (
    "import pathlib, subprocess\n"
    "from blizzard_mock.harness.engine import current_context\n"
    "ctx = current_context()\n"
    f"ctx.cwd = pathlib.Path(ctx.cwd) / {REPO_NAME!r}\n"
    f"apply_diff({_OPENCODE_TRANSCRIPT_DIFF!r})\n"
    f"commit({_OPENCODE_TRANSCRIPT_COMMIT_MESSAGE!r})\n"
    "_repo_dir = str(ctx.cwd)\n"
    "_branch = subprocess.run(\n"
    '    ["git", "-C", _repo_dir, "rev-parse", "--abbrev-ref", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    "_commit = subprocess.run(\n"
    '    ["git", "-C", _repo_dir, "rev-parse", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    'subprocess.run(["git", "-C", _repo_dir, "push", "origin", _branch], check=True, capture_output=True)\n'
    "subprocess.run(\n"
    '    ["blizzard", "runner", "artifact", "commit",\n'
    f'     "--repo", {REPO_NAME!r}, "--branch", _branch, "--commit", _commit],\n'
    "    check=True, capture_output=True,\n"
    ")\n"
)


def _opencode_transcript_chunk_spec(work_ref_url: str) -> dict:
    """A scripted build -> deliver chunk, entirely under OpenCode, whose build node mints
    real ``Edit``/``Bash`` tool turns for the runner's transcript API to read back."""
    return {
        "graph_id": "gr_opencode_transcript",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "session": "resume",
                "session_harnesses": ["opencode"],
                "judged_by": "worker",
                "prompt": _OPENCODE_TRANSCRIPT_BUILD_SCRIPT,
                "judgement_prompt": JUDGEMENT_SCRIPT,
                "choices": [{"name": "pass", "description": "committed and green", "to": "deliver"}],
                "retries_max": 1,
            },
            "deliver": {
                "executor": "hub",
                "run": [{"command": "true"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Every repo merged cleanly.", "to": "done"},
                        "conflict": {"description": "A repo did not merge cleanly.", "to": "build"},
                    },
                },
            },
        },
        "work_refs": [{"source": "mock", "ref": work_ref_url}],
    }


def test_opencode_transcript_is_read_back_through_the_runner_http_api(tmp_path: Path) -> None:
    """A real ``mock-opencode`` subprocess mints a genuine session export; read back
    through the runner's own local HTTP API via :class:`OpenCodeTranscriptSource`. Pins
    provenance: the Bash turn's tool output carries the real commit sha, cross-checked
    against the bare origin — the same shape Claude Code's own counterpart proves."""
    bin_dir = require_mock_fleet()
    workspace, _origins, origin_bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    transcripts_root = tmp_path / "transcripts"
    fenced = _tick_env()
    fenced["BZ_TRANSCRIPTS_ROOT"] = str(transcripts_root)

    hub_port = _free_port()
    # Real `opencode export` resolves a session id from a global daemon store, so the
    # production exporter passes no env (`opencode_export.py`'s own comment). The MOCK's
    # `export` subcommand instead re-derives its root from `BZ_TRANSCRIPTS_ROOT` on every
    # invocation, so — unlike the ticks-only fencing `_drive` uses — this must stay live in
    # the real process env for the runner's later, out-of-band transcript read too.
    prior_transcripts_root = os.environ.get("BZ_TRANSCRIPTS_ROOT")
    os.environ["BZ_TRANSCRIPTS_ROOT"] = str(transcripts_root)
    try:
        with mock_hub(bin_dir, hub_port) as hub:
            seeded = hub.post("/_seed/chunk", json=_opencode_transcript_chunk_spec(_WORK_REF_URL))
            assert seeded.status_code == 201, seeded.text
            chunk_id = seeded.json()["chunk_id"]

            config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
            config = dataclasses.replace(
                config, host="127.0.0.1", port=_free_port(), transcripts_root=str(transcripts_root)
            )

            with _runner_api(config):
                landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
                assert landed, f"chunk did not land under OpenCode (status {_status(hub, chunk_id)!r})"

                runner_client = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=10.0)
                try:
                    leases = runner_client.get("/api/leases")
                    assert leases.status_code == 200, leases.text
                    matches = [item for item in leases.json()["items"] if item["chunk_id"] == chunk_id]
                    assert len(matches) == 1, f"expected exactly one lease for the chunk, got {matches!r}"
                    lease_id = matches[0]["lease_id"]

                    transcript = runner_client.get(f"/api/leases/{lease_id}/transcript")
                    assert transcript.status_code == 200, transcript.text
                    body = transcript.json()
                finally:
                    runner_client.close()
    finally:
        if prior_transcripts_root is None:
            os.environ.pop("BZ_TRANSCRIPTS_ROOT", None)
        else:
            os.environ["BZ_TRANSCRIPTS_ROOT"] = prior_transcripts_root

    assert body["available"] is True, body
    assert body["session_id"], "a closed lease's session id must still be readable"
    turns = body["turns"]
    kinds = [t["kind"] for t in turns]
    assert "env" in kinds, f"no env (spawn) turn: {kinds}"
    assert "asst" in kinds, f"no asst (verdict) turn: {kinds}"

    tool_turns = {t["tool"]["name"]: t["tool"] for t in turns if t["kind"] == "tool"}
    assert "Edit" in tool_turns, f"no Edit tool turn: {turns}"
    assert "Bash" in tool_turns, f"no Bash tool turn: {turns}"
    edit_tool = tool_turns["Edit"]
    bash_tool = tool_turns["Bash"]
    assert edit_tool["output"], "the Edit turn's tool output was never filled in"
    assert bash_tool["output"], "the Bash turn's tool output was never filled in"
    assert isinstance(bash_tool["input"], dict), f"tool input is not structured: {bash_tool!r}"

    real_sha = _git_bare(origin_bare, "log", "--all", "--format=%H", "-1", "--", "transcript-proof.txt").strip()
    assert real_sha, "the mock harness's commit never reached the bare origin"
    assert real_sha[:7] in bash_tool["output"], (
        f"transcript tool output does not carry the real commit sha ({real_sha[:7]!r} not in {bash_tool['output']!r})"
    )
    assert _OPENCODE_TRANSCRIPT_COMMIT_MESSAGE in bash_tool["output"]

    # The build script itself never calls `ask`/`verdict` — its own assistant turn closes
    # with empty text — so the judgement's verdict is the LAST asst turn, not the first.
    asst_turn = next(t for t in reversed(turns) if t["kind"] == "asst")
    assert "pass" in asst_turn["text"]
    assert "the mock harness committed the change; checks are green" in asst_turn["text"]


#: Fires once — a marker file in the acquired worktree, so it survives the crashed lease's
#: own death and the requeued lease's brand new OpenCode session sees it and skips the crash.
_OPENCODE_CRASH_MARKER = "crash-recovered-once.marker"

#: The judgement resume's own completion signal — absent, it emits no verdict at all, a
#: genuinely verdict-less judgement rather than a rubber stamp over uncommitted work.
_OPENCODE_CRASH_DONE_MARKER = "build-done.marker"

#: Crashes hard (``os._exit``, no envelope, no ``SessionEnd``) on its very first invocation,
#: right after recording one real tool call — proving the transcript captured that call
#: before the process died. A requeued retry's fresh session finds the marker already
#: written and does the real work instead.
_OPENCODE_CRASH_BUILD_SCRIPT = (
    "import pathlib, subprocess\n"
    f"marker = pathlib.Path({_OPENCODE_CRASH_MARKER!r})\n"
    "if not marker.exists():\n"
    "    tool_call('Edit', {'path': 'partial.txt'}, output='edited just before the crash')\n"
    "    marker.write_text('crashed once\\n')\n"
    "    crash(hard=True)\n"
    "from blizzard_mock.harness.engine import current_context\n"
    "ctx = current_context()\n"
    f"ctx.cwd = pathlib.Path(ctx.cwd) / {REPO_NAME!r}\n"
    f"apply_diff({_OPENCODE_TRANSCRIPT_DIFF!r})\n"
    f"commit({_OPENCODE_TRANSCRIPT_COMMIT_MESSAGE!r})\n"
    "_repo_dir = str(ctx.cwd)\n"
    "_branch = subprocess.run(\n"
    '    ["git", "-C", _repo_dir, "rev-parse", "--abbrev-ref", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    "_commit = subprocess.run(\n"
    '    ["git", "-C", _repo_dir, "rev-parse", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    'subprocess.run(["git", "-C", _repo_dir, "push", "origin", _branch], check=True, capture_output=True)\n'
    "subprocess.run(\n"
    '    ["blizzard", "runner", "artifact", "commit",\n'
    f'     "--repo", {REPO_NAME!r}, "--branch", _branch, "--commit", _commit],\n'
    "    capture_output=True,\n"  # best-effort — no live local API in this tier; never gates the build's own done marker
    ")\n"
    f"pathlib.Path({_OPENCODE_CRASH_DONE_MARKER!r}).write_text('done\\n')\n"
)

#: Resumed against the crashed session (judged_by=worker fires regardless of how the build's
#: own turn exited) — a rubber-stamped pass would hide a crash that landed nothing, so this
#: checks for the real work's own completion marker instead of trusting the exit alone.
_OPENCODE_CRASH_JUDGEMENT_SCRIPT = (
    "import pathlib\n"
    f"if pathlib.Path({_OPENCODE_CRASH_DONE_MARKER!r}).exists():\n"
    "    verdict('pass', 'the mock harness committed the change; checks are green')\n"
)


def _opencode_crash_chunk_spec(work_ref_url: str) -> dict:
    """A build -> deliver chunk whose build node hard-crashes its first OpenCode invocation
    mid-turn, then recovers through a genuine requeue onto a fresh session (blizzard#437
    Phase 4's boundary-scoped usage recovery, proven for OpenCode specifically)."""
    return {
        "graph_id": "gr_opencode_crash_recovery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "session": "resume",
                "session_harnesses": ["opencode"],
                "judged_by": "worker",
                "prompt": _OPENCODE_CRASH_BUILD_SCRIPT,
                "judgement_prompt": _OPENCODE_CRASH_JUDGEMENT_SCRIPT,
                "choices": [{"name": "pass", "description": "committed and green", "to": "deliver"}],
                "retries_max": 1,
            },
            "deliver": {
                "executor": "hub",
                "run": [{"command": "true"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Every repo merged cleanly.", "to": "done"},
                        "conflict": {"description": "A repo did not merge cleanly.", "to": "build"},
                    },
                },
            },
        },
        "work_refs": [{"source": "mock", "ref": work_ref_url}],
    }


def _leases_for_chunk(config: RunnerConfig, chunk_id: str) -> list[dict]:
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(leases).where(leases.c.chunk_id == chunk_id)).mappings()]
    finally:
        engine.dispose()


def _usage_facts_for_chunk(config: RunnerConfig, chunk_id: str) -> list[dict]:
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.connect() as conn:
            query = select(usage_facts).where(usage_facts.c.chunk_id == chunk_id)
            return [dict(row) for row in conn.execute(query).mappings()]
    finally:
        engine.dispose()


def test_opencode_crash_mid_turn_recovers_with_usage_recorded_exactly_once(tmp_path: Path) -> None:
    """A real ``mock-opencode`` subprocess dies hard mid-build, right after one recorded
    tool call — no envelope, no ``SessionEnd``. The runner's own boundary-scoped recovery
    (blizzard#437 Phase 4) resumes that SAME crashed session for its judgement, finds no
    completed work, fails the attempt, and requeues a fresh lease that redoes the build
    clean. The chunk still lands, and no generation's usage is ever recorded twice or
    double-charged for tokens a prior generation already recorded."""
    bin_dir = require_mock_fleet()
    workspace, _origins, origin_bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    transcripts_root = tmp_path / "transcripts"
    fenced = _tick_env()
    fenced["BZ_TRANSCRIPTS_ROOT"] = str(transcripts_root)

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/chunk", json=_opencode_crash_chunk_spec(_WORK_REF_URL))
        assert seeded.status_code == 201, seeded.text
        chunk_id = seeded.json()["chunk_id"]

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, transcripts_root=str(transcripts_root))

        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
        assert landed, f"chunk did not land after the crash (status {_status(hub, chunk_id)!r})"

    real_sha = _git_bare(origin_bare, "log", "--all", "--format=%H", "-1", "--", "transcript-proof.txt").strip()
    assert real_sha, "the retried build's commit never reached the bare origin"

    lease_rows = sorted(_leases_for_chunk(config, chunk_id), key=lambda row: row["epoch"])
    assert len(lease_rows) == 2, f"expected the crashed lease and exactly one requeued retry, got {lease_rows!r}"
    crashed_lease_id, retried_lease_id = lease_rows[0]["lease_id"], lease_rows[1]["lease_id"]

    usage_rows = _usage_facts_for_chunk(config, chunk_id)
    seen: set[tuple[str, int, str]] = set()
    for row in usage_rows:
        key = (row["lease_id"], row["generation"], row["kind"])
        assert key not in seen, f"usage recorded twice for the same generation/kind: {key} in {usage_rows!r}"
        seen.add(key)
    by_lease: dict[str, dict[str, dict]] = {}
    for row in usage_rows:
        by_lease.setdefault(row["lease_id"], {})[row["kind"]] = row

    # The crashed generation left no completed assistant turn behind — its own worker
    # invocation earns no usage fact at all (never a fabricated partial sum) — but its
    # judgement resume genuinely ran (that is what discovered the crash and requeued),
    # so it earns its own real fact.
    assert set(by_lease[crashed_lease_id]) == {"judge"}, by_lease[crashed_lease_id]
    assert by_lease[crashed_lease_id]["judge"]["cost_usd"] is not None
    # The retry's own worker invocation exited clean with a real envelope this time —
    # both its spawn and its judge land their own genuine, un-fabricated facts.
    assert set(by_lease[retried_lease_id]) == {"spawn", "judge"}, by_lease[retried_lease_id]
    assert by_lease[retried_lease_id]["spawn"]["cost_usd"] is not None
    assert by_lease[retried_lease_id]["judge"]["cost_usd"] is not None
