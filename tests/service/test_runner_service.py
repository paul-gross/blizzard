"""Runner service tier — the real runner against the mock hub (verification/blizzard.md).

The **runner** daemon's reconciliation loop is exercised from outside, driven one real
``run_single_tick`` at a time (the steppable driver, ``bzh:steppable-loop``) against a
**mock hub** run as its own subprocess — the counterpart mocked (``implementation/
mocking.md``, "the runner → run it against the mock hub"). The mock hub's levers
manufacture the rare states a real hub could only be contrived into, so the tick's
resilience logic is asserted directly:

* **unreachable hub → buffered completion** — the completion is store-and-forward durable
  : while the hub is down the chunk never advances and the runner's outbound buffer
  holds the fact; when the hub heals the buffered completion flushes and the chunk lands.
* **dropped ack → idempotent re-apply** — the hub applies the transition but drops the ack
  (503); the runner re-flushes the same completion and the hub's epoch-idempotent apply
   advances the chunk exactly once — no double transition — through to done.
* **stale envelope tolerated** — the hub serves a stale-epoch envelope; the runner fences
  its completion on its own lease epoch (not the envelope's), so the chunk still lands.

A fourth scenario proves a different seam: **the transcript read at fleet tier**
(issue #29's provenance gap). A real ``mock-claude-code`` mints a genuine Claude-shaped
JSONL transcript as it drives the build node, and the test reads it back through the
**runner's own local HTTP API** (``GET /api/leases`` then
``GET /api/leases/{lease_id}/transcript``) — not by importing the parser, which would
re-close the loop the mock's transcript writer exists to open.

Every seam is real (fixture workspace, mock-claude-code, git), no tokens, no network.
Reproduce — from a provisioned feature env — with::

    BLIZZARD_SERVICE=1 uv run pytest tests/service/test_runner_service.py
"""

from __future__ import annotations

import dataclasses
import os
import time
from pathlib import Path

import httpx
import pytest

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop.build import run_single_tick
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from tests.e2e.test_acceptance_loop import REPO, REPO_NAME, _free_port, _git_bare, _runner_api, _runner_config
from tests.service.support import (
    JUDGEMENT_SCRIPT,
    mint_fixture,
    mock_hub,
    mock_hub_chunk_spec,
    poll_until,
    require_mock_fleet,
    require_winter_source,
    service_gate,
)

pytestmark = [pytest.mark.service, service_gate]

_PM_URL = f"{REPO}/issues/1"


def _tick_env() -> dict[str, str]:
    fenced = dict(os.environ)
    fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
    return fenced


def _drive(config: RunnerConfig, fenced: dict[str, str], *, ticks: int, pause: float = 0.5) -> None:
    """Run ``ticks`` synchronous reconciliation passes with the harness fence set."""
    prior = dict(os.environ)
    os.environ.update(fenced)
    try:
        for _ in range(ticks):
            run_single_tick(config)
            time.sleep(pause)
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _status(hub: httpx.Client, chunk_id: str) -> str:
    return hub.get(f"/api/chunks/{chunk_id}").json()["status"]


def _pending_outbound(config: RunnerConfig) -> int:
    """The depth of the runner's store-and-forward buffer."""
    engine = create_engine_from_url(config.db_url)
    try:
        return len(SqlAlchemyRunnerStore(engine).pending_outbound())
    finally:
        engine.dispose()


def _seed(hub: httpx.Client) -> str:
    resp = hub.post("/_seed/chunk", json=mock_hub_chunk_spec(_PM_URL))
    assert resp.status_code == 201, resp.text
    return resp.json()["chunk_id"]


def test_unreachable_hub_buffers_the_completion_then_lands_on_recovery(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    workspace, _origins, origin_bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        chunk_id = _seed(hub)
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        # Drive (hub up) until the mock worker has committed, exited, and ADVANCE has
        # *buffered* the completion — the tick boundary just before PULL would flush it
        # (ADVANCE enqueues; the flush is the next tick's PULL, so we can wedge in here).
        buffered = poll_until(lambda: _tick_then(config, fenced, lambda: _pending_outbound(config) >= 1), timeout=60.0)
        assert buffered, "the completion never buffered (the worker did not run to completion)"
        assert _status(hub, chunk_id) != "done", "the chunk landed before the outage could be staged"

        # Now the hub goes unreachable: every flush attempt fails, so the completion stays
        # store-and-forward buffered. The chunk's status is unreadable *because* the
        # hub is down — which is the point — so the buffer depth is the proof it did not flush.
        assert hub.post("/_levers/unreachable", json={"remaining": 10_000}).status_code == 200
        _drive(config, fenced, ticks=4)
        assert _pending_outbound(config) >= 1, "the completion did not stay buffered during the outage"

        # Heal the hub; the buffered completion flushes and the chunk lands.
        assert hub.post("/_levers/reset").status_code == 200
        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=60.0)
        assert landed, f"chunk did not land after recovery (status {_status(hub, chunk_id)!r})"
        assert _pending_outbound(config) == 0, "the outbound buffer did not drain after recovery"

    # The runner pushed the mock harness's commit to the bare origin (the artifact-push half
    # of ADVANCE, D-026) — on the work branch. Unlike e2e, the mock hub fakes the deliver
    # node, so the commit is not merged to main; it is reachable across the origin's refs.
    reachable = _git_bare(origin_bare, "log", "--all", "--name-only", "--format=")
    assert "LANDED.md" in reachable.split(), "the mock harness's commit never reached the bare origin"


def test_dropped_ack_reapplies_idempotently_through_to_done(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        chunk_id = _seed(hub)
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        # Drop the very first completion ack: the hub advances build -> deliver but answers
        # 503, so the runner keeps the completion buffered and re-flushes it. The hub's
        # epoch-idempotent apply must advance the chunk exactly once.
        assert hub.post("/_levers/drop_ack", json={"chunk_id": chunk_id, "remaining": 1}).status_code == 200
        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
        assert landed, f"chunk did not land after the dropped ack (status {_status(hub, chunk_id)!r})"
        # done is reached once — a double apply would have errored or re-run the deliver node.
        assert _status(hub, chunk_id) == "done"


def test_stale_envelope_is_tolerated_and_the_chunk_still_lands(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        chunk_id = _seed(hub)
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        # Serve a stale-epoch envelope on the re-read: the runner fences its completion on
        # its own lease epoch, not the envelope's, so a stale envelope is benign — the chunk
        # still lands. (The lever is single-shot; it perturbs one envelope read.)
        assert hub.post("/_levers/stale_envelope", json={"chunk_id": chunk_id, "remaining": 1}).status_code == 200
        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
        assert landed, f"chunk did not land despite a stale envelope (status {_status(hub, chunk_id)!r})"


# --------------------------------------------------------------------------- #
# Transcript provenance — the panel's read proven at fleet tier (issue #29).
# --------------------------------------------------------------------------- #

#: A real unified diff, applied for real by the mock's own ``git apply`` plumbing
#: (the exact shape proven in ``blizzard-mock`` ``test_harness_smoke.py``'s
#: ``test_script_applies_diff_and_makes_real_commit``).
_TRANSCRIPT_DIFF = (
    "diff --git a/transcript-proof.txt b/transcript-proof.txt\n"
    "new file mode 100644\n"
    "index 0000000..1111111\n"
    "--- /dev/null\n"
    "+++ b/transcript-proof.txt\n"
    "@@ -0,0 +1 @@\n"
    "+minted by the real mock-claude-code transcript writer\n"
)

_TRANSCRIPT_COMMIT_MESSAGE = "feat: mint a transcript-provable commit"

#: Unlike ``BUILD_SCRIPT`` above (raw ``subprocess`` git calls, which the transcript
#: writer never sees), this script calls the mock's own ``apply_diff``/``commit``
#: helpers so the run mints matched ``tool_use``/``tool_result`` turns
#: (``Edit``/``Bash``) — exactly the pairing ``blizzard-mock``'s
#: ``helpers._record_tool_turn`` performs for a real behavior script. The helpers
#: apply against ``current_context().cwd`` directly, which the runner sets to the
#: *environment* directory (holding every acquired repo as a child) rather
#: than the one repo the fixture uses — so the script first repoints the ambient
#: context's ``cwd`` at the repo child directory, the same real object the engine's
#: own ``run_prompt`` constructed, before calling the helpers.
_TRANSCRIPT_BUILD_SCRIPT = (
    "import pathlib\n"
    "from blizzard_mock.harness.engine import current_context\n"
    "ctx = current_context()\n"
    f"ctx.cwd = pathlib.Path(ctx.cwd) / {REPO_NAME!r}\n"
    f"apply_diff({_TRANSCRIPT_DIFF!r})\n"
    f"commit({_TRANSCRIPT_COMMIT_MESSAGE!r})\n"
)


def _transcript_chunk_spec(pm_url: str) -> dict:
    """A scripted build -> deliver chunk whose build node mints tool turns (issue #29)."""
    return {
        "graph_id": "gr_transcript",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "session": "resume",
                "judged_by": "worker",
                "prompt": _TRANSCRIPT_BUILD_SCRIPT,
                "judgement_prompt": JUDGEMENT_SCRIPT,
                "choices": [{"name": "pass", "description": "committed and green", "to": "deliver"}],
                "retries_max": 1,
            },
            "deliver": {"executor": "hub", "mode": "merge-to-main"},
        },
        "pm_pointers": [{"source": "mock", "ref": pm_url}],
    }


def test_transcript_is_read_back_through_the_runner_http_api(tmp_path: Path) -> None:
    """The panel's transcript read, proven at the tier that can now reach it.

    Until the mock fleet grew a real transcript writer, every transcript test proved
    the parser against fixtures the same repo authored — a closed loop; production
    could have been serving ``not_found`` for every real agent and every test would
    stay green. This drives a chunk through the **real** fleet — a real
    ``mock-claude-code`` subprocess mints a genuine Claude-shaped JSONL as it runs
    ``apply_diff``/``commit`` — then reads the result back through the **runner's
    own local HTTP API**, never by importing the parser (that stays the unit tier's
    job and would re-close the loop).

    The provenance assertion: the ``Bash`` (commit) turn's ``tool_output`` carries a
    short commit sha the transcript writer minted *from the real ``git commit`` this
    run made* — cross-checked here against the actual commit reachable on the bare
    origin. No fixture or stub can satisfy this: the sha does not exist until the
    real commit runs.
    """
    bin_dir = require_mock_fleet()
    workspace, _origins, origin_bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    transcripts_root = tmp_path / "transcripts"
    fenced = _tick_env()
    fenced["BZ_TRANSCRIPTS_ROOT"] = str(transcripts_root)

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/chunk", json=_transcript_chunk_spec(_PM_URL))
        assert seeded.status_code == 201, seeded.text
        chunk_id = seeded.json()["chunk_id"]

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(
            config, host="127.0.0.1", port=_free_port(), transcripts_root=str(transcripts_root)
        )

        with _runner_api(config):
            landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
            assert landed, f"chunk did not land (status {_status(hub, chunk_id)!r})"

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

    # The transcript is available and genuinely parsed, not a stub 404/`not_found` —
    # the closed loop this test exists to break.
    assert body["available"] is True, body
    assert body["session_id"], "a closed lease's session id must still be readable"
    turns = body["turns"]
    kinds = [t["kind"] for t in turns]
    assert "env" in kinds, f"no env (spawn) turn: {kinds}"
    assert "asst" in kinds, f"no asst (verdict) turn: {kinds}"

    tool_turns = {t["tool_name"]: t for t in turns if t["kind"] == "tool"}
    assert "Edit" in tool_turns, f"no Edit tool turn: {turns}"
    assert "Bash" in tool_turns, f"no Bash tool turn: {turns}"
    edit_turn = tool_turns["Edit"]
    bash_turn = tool_turns["Bash"]
    assert edit_turn["tool_output"], "the Edit turn's tool_output was never filled in"
    assert bash_turn["tool_output"], "the Bash turn's tool_output was never filled in"

    # Provenance: the real commit sha, independently read off the bare origin (the
    # runner's own artifact push), must appear in the transcript's own
    # tool_output — content that only exists once the real `git commit` ran.
    real_sha = _git_bare(origin_bare, "log", "--all", "--format=%H", "-1", "--", "transcript-proof.txt").strip()
    assert real_sha, "the mock harness's commit never reached the bare origin"
    assert real_sha[:7] in bash_turn["tool_output"], (
        f"transcript tool_output does not carry the real commit sha "
        f"({real_sha[:7]!r} not in {bash_turn['tool_output']!r})"
    )
    assert _TRANSCRIPT_COMMIT_MESSAGE in bash_turn["tool_output"]

    # The verdict landed as the final asst turn, carrying the judgement's own text.
    asst_turn = next(t for t in turns if t["kind"] == "asst")
    assert "pass" in asst_turn["text"]
    assert "the mock harness committed the change; checks are green" in asst_turn["text"]


def _run_and_check(config: RunnerConfig, fenced: dict[str, str], hub: httpx.Client, chunk_id: str, target: str) -> bool:
    """One tick + a status read — the poll predicate the scenarios share."""
    _drive(config, fenced, ticks=1, pause=0.3)
    return _status(hub, chunk_id) == target


def _tick_then(config: RunnerConfig, fenced: dict[str, str], check) -> bool:
    """One tick, then evaluate ``check`` — the buffered-completion poll predicate."""
    _drive(config, fenced, ticks=1, pause=0.3)
    return bool(check())
