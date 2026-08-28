"""Runner service tier — the real runner against the mock hub (verification/blizzard.md).

The runner's loop is driven one tick at a time against a mock hub whose levers
manufacture rare states: unreachable hub, dropped ack, stale envelope — plus two scenarios
reading a real transcript back through the runner's own local HTTP API (#29, blizzard#249).
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import select

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.lease_auth import LeaseToken
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.loop.build import LoopWiring
from blizzard.runner.runtime import init_environment as init_runner_environment
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.runner.store.schema import escalation_closures
from tests.e2e.test_acceptance_loop import (
    REPO,
    REPO_NAME,
    _await_http,
    _free_port,
    _git_bare,
    _runner_api,
    _runner_config,
    _terminate,
)
from tests.service.support import (
    JUDGEMENT_SCRIPT,
    SseTap,
    mint_fixture,
    mock_hub,
    mock_hub_chunk_spec,
    mock_hub_escalating_chunk_spec,
    poll_until,
    require_mock_fleet,
    require_winter_source,
    service_gate,
    sse_tap,
    transcript_segment_record,
    transcript_segment_turn,
)
from tests.support import daemon_log_sink, parse_sse_frames

pytestmark = [pytest.mark.service, service_gate]

_WORK_REF_URL = f"{REPO}/issues/1"


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
            LoopWiring.of(config).tick_once()
            time.sleep(pause)
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _status(hub: httpx.Client, chunk_id: str) -> str:
    """The mock hub's own status read."""
    return hub.get(f"/api/fleet/chunks/{chunk_id}").json()["status"]


def _pending_outbound(config: RunnerConfig) -> int:
    """The depth of the runner's store-and-forward buffer."""
    engine = create_engine_from_url(config.db_url)
    try:
        return len(SqlAlchemyRunnerStore(engine).pending_outbound())
    finally:
        engine.dispose()


def _seed(hub: httpx.Client) -> str:
    resp = hub.post("/_seed/chunk", json=mock_hub_chunk_spec(_WORK_REF_URL))
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
        # *buffered* the completion — the tick boundary just before PULL would flush it.
        buffered = poll_until(lambda: _tick_then(config, fenced, lambda: _pending_outbound(config) >= 1), timeout=60.0)
        assert buffered, "the completion never buffered (the worker did not run to completion)"
        assert _status(hub, chunk_id) != "done", "the chunk landed before the outage could be staged"

        # Now the hub goes unreachable: every flush attempt fails, so the completion stays
        # store-and-forward buffered — the buffer depth is the proof it did not flush.
        assert hub.post("/_levers/unreachable", json={"remaining": 10_000}).status_code == 200
        _drive(config, fenced, ticks=4)
        assert _pending_outbound(config) >= 1, "the completion did not stay buffered during the outage"

        # Heal the hub; the buffered completion flushes and the chunk lands.
        assert hub.post("/_levers/reset").status_code == 200
        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=60.0)
        assert landed, f"chunk did not land after recovery (status {_status(hub, chunk_id)!r})"
        assert _pending_outbound(config) == 0, "the outbound buffer did not drain after recovery"

    # The runner pushed the mock harness's commit to the bare origin, on the work branch —
    # the mock hub fakes deliver, so it is reachable across the origin's refs, not merged.
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
        # 503, so the runner re-flushes; the hub's epoch-idempotent apply advances it once.
        assert hub.post("/_levers/drop_ack", json={"chunk_id": chunk_id, "remaining": 1}).status_code == 200
        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
        assert landed, f"chunk did not land after the dropped ack (status {_status(hub, chunk_id)!r})"
        # done is reached once — a double apply would have errored or re-run the deliver node.
        assert _status(hub, chunk_id) == "done"


def _pending_transcript_outbound(config: RunnerConfig) -> int:
    """The depth of the runner's transcript-lane buffer — D3's own, never the fact lane's."""
    engine = create_engine_from_url(config.db_url)
    try:
        return len(SqlAlchemyRunnerStore(engine).pending_transcript_outbound())
    finally:
        engine.dispose()


def test_transcript_route_failure_never_blocks_the_fact_lane(tmp_path: Path) -> None:
    """D6: with the transcript route 503ing, the fact lane still lands the chunk while
    transcript facts buffer; the backlog flushes with no loss or duplication on return."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    transcripts_root = tmp_path / "transcripts"
    fenced = _tick_env()
    fenced["BZ_TRANSCRIPTS_ROOT"] = str(transcripts_root)

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        chunk_id = _seed(hub)
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, transcripts_root=str(transcripts_root), transcripts_ship=True)

        assert hub.post("/_levers/unreachable_transcripts", json={"remaining": 10_000}).status_code == 200

        # The fact lane is unaffected by the transcript route's outage: the chunk lands.
        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
        assert landed, f"the chunk did not land while the transcript route failed (status {_status(hub, chunk_id)!r})"
        # The transcript lane, meanwhile, buffered rather than silently dropping its facts —
        # at minimum the closed lease's own final marker, enqueued regardless of the outage.
        backlog = _pending_transcript_outbound(config)
        assert backlog >= 1, "no transcript fact buffered during the outage"

        assert hub.post("/_levers/reset").status_code == 200  # clears levers only, not seeded chunk state
        drained = poll_until(
            lambda: _tick_then(config, fenced, lambda: _pending_transcript_outbound(config) == 0), timeout=60.0
        )
        assert drained, "the transcript backlog never flushed after the route recovered"
        # Still done, not re-driven past it — the recovery flush touched no chunk state.
        assert _status(hub, chunk_id) == "done"


def test_transcript_route_slow_never_blocks_the_fact_lane(tmp_path: Path) -> None:
    """D6's other half (review F18): the test above proves wedged (hard-down);
    `delay_transcripts` proves the route can answer, slowly, and the chunk still lands."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    transcripts_root = tmp_path / "transcripts"
    fenced = _tick_env()
    fenced["BZ_TRANSCRIPTS_ROOT"] = str(transcripts_root)

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        chunk_id = _seed(hub)
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, transcripts_root=str(transcripts_root), transcripts_ship=True)

        # Global, not chunk-scoped: the transcripts route's path carries no chunk id of its
        # own (each record names its chunk in the request body, not the URL).
        assert hub.post("/_levers/delay_transcripts", json={"payload": {"ms": 300}}).status_code == 200

        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
        assert landed, f"the chunk did not land while the transcript route was slow (status {_status(hub, chunk_id)!r})"

        drained = poll_until(
            lambda: _tick_then(config, fenced, lambda: _pending_transcript_outbound(config) == 0), timeout=60.0
        )
        assert drained, "the transcript backlog never fully flushed despite the route staying up (just slow)"
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
        # its own lease epoch, not the envelope's, so a stale envelope is benign.
        assert hub.post("/_levers/stale_envelope", json={"chunk_id": chunk_id, "remaining": 1}).status_code == 200
        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
        assert landed, f"chunk did not land despite a stale envelope (status {_status(hub, chunk_id)!r})"


def _open_escalation_chunk_ids(config: RunnerConfig) -> list[str]:
    engine = create_engine_from_url(config.db_url)
    try:
        return [escalation.chunk_id for escalation in SqlAlchemyRunnerStore(engine).open_escalations()]
    finally:
        engine.dispose()


def _lease_id_for_chunk(config: RunnerConfig, chunk_id: str) -> str | None:
    engine = create_engine_from_url(config.db_url)
    try:
        for lease in SqlAlchemyRunnerStore(engine).list_active_leases():
            if lease.chunk_id == chunk_id:
                return lease.lease_id
        return None
    finally:
        engine.dispose()


def _lease_closure_reason(config: RunnerConfig, lease_id: str) -> str | None:
    """Discriminates PULL's abandon (``released``) from an ordinary completion racing it to
    ``done`` (``transitioned``) — both empty the same active-leases row."""
    engine = create_engine_from_url(config.db_url)
    try:
        for closed in SqlAlchemyRunnerStore(engine).list_closed_leases(50):
            if closed.lease.lease_id == lease_id:
                return closed.reason
        return None
    finally:
        engine.dispose()


def _escalation_closure_reason(config: RunnerConfig, chunk_id: str) -> str | None:
    """Distinct from mere non-openness, which a re-adopted lease also causes with no row here."""
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                select(escalation_closures.c.reason)
                .where(escalation_closures.c.chunk_id == chunk_id)
                .order_by(escalation_closures.c.closed_at.desc())
                .limit(1)
            ).first()
        return None if row is None else str(row[0])
    finally:
        engine.dispose()


def test_pull_abandons_the_active_lease_when_the_hub_reports_the_chunk_stopped(tmp_path: Path) -> None:
    """Issue #118's backstop, driven over a real hub response rather than ``FakeHub``."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        chunk_id = _seed(hub)
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        _drive(config, fenced, ticks=1)
        lease_id = _lease_id_for_chunk(config, chunk_id)
        assert lease_id is not None, "lease never went active"

        assert hub.post("/_seed/stop", json={"chunk_id": chunk_id}).status_code == 200

        closed = poll_until(
            lambda: _tick_then(config, fenced, lambda: _lease_closure_reason(config, lease_id) is not None),
            timeout=60.0,
        )
        assert closed, "lease never closed after the hub reported the chunk stopped"
        assert _lease_closure_reason(config, lease_id) == "released", (
            "lease closed by ordinary completion racing the stop, not PULL's own abandon"
        )


def test_pull_closes_the_local_escalation_when_the_hub_reports_the_chunk_stopped(tmp_path: Path) -> None:
    """Issues #292/#293's sweep, driven over a real hub response."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        resp = hub.post("/_seed/chunk", json=mock_hub_escalating_chunk_spec(_WORK_REF_URL))
        assert resp.status_code == 201, resp.text
        chunk_id = resp.json()["chunk_id"]
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        escalated = poll_until(
            lambda: _tick_then(config, fenced, lambda: chunk_id in _open_escalation_chunk_ids(config)),
            timeout=60.0,
        )
        assert escalated, f"chunk never escalated (hub status {_status(hub, chunk_id)!r})"

        assert hub.post("/_seed/stop", json={"chunk_id": chunk_id}).status_code == 200

        closed = poll_until(
            lambda: _tick_then(config, fenced, lambda: _escalation_closure_reason(config, chunk_id) is not None),
            timeout=60.0,
        )
        assert closed, "escalation closure never recorded after the hub reported the chunk stopped"
        assert _escalation_closure_reason(config, chunk_id) == "stopped", (
            "escalation cleared by something other than PULL's own stopped-chunk closure"
        )


# Transcript provenance — the panel's read proven at fleet tier (issue #29).

#: A real unified diff, applied for real by the mock's own ``git apply`` plumbing (see
#: ``blizzard-mock``'s ``test_script_applies_diff_and_makes_real_commit``).
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

#: Calls the mock's own ``apply_diff``/``commit`` helpers (not raw subprocess) so the run
#: mints matched ``Edit``/``Bash`` tool turns; repoints ``cwd`` to the repo child dir first.
_TRANSCRIPT_BUILD_SCRIPT = (
    "import pathlib, subprocess\n"
    "from blizzard_mock.harness.engine import current_context\n"
    "ctx = current_context()\n"
    f"ctx.cwd = pathlib.Path(ctx.cwd) / {REPO_NAME!r}\n"
    f"apply_diff({_TRANSCRIPT_DIFF!r})\n"
    f"commit({_TRANSCRIPT_COMMIT_MESSAGE!r})\n"
    # Push the branch and declare it (issue #143) — the worker does this itself,
    # through the real `blizzard runner artifact commit` verb.
    "_repo_dir = str(ctx.cwd)\n"
    "_branch = subprocess.run(\n"
    '    ["git", "-C", _repo_dir, "rev-parse", "--abbrev-ref", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    "_commit = subprocess.run(\n"
    '    ["git", "-C", _repo_dir, "rev-parse", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    'subprocess.run(["git", "-C", _repo_dir, "push", "origin", _branch], check=True)\n'
    "subprocess.run(\n"
    '    ["blizzard", "runner", "artifact", "commit",\n'
    f'     "--repo", {REPO_NAME!r}, "--branch", _branch, "--commit", _commit],\n'
    "    check=True,\n"
    ")\n"
)


def _transcript_chunk_spec(work_ref_url: str) -> dict:
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


def test_transcript_is_read_back_through_the_runner_http_api(tmp_path: Path) -> None:
    """A real mock-claude-code subprocess mints a genuine transcript; read back through
    the runner's own local HTTP API. Pins provenance: the Bash turn's tool output carries
    the real commit sha, cross-checked against the bare origin."""
    bin_dir = require_mock_fleet()
    workspace, _origins, origin_bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    transcripts_root = tmp_path / "transcripts"
    fenced = _tick_env()
    fenced["BZ_TRANSCRIPTS_ROOT"] = str(transcripts_root)

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/chunk", json=_transcript_chunk_spec(_WORK_REF_URL))
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

    # The transcript is available and genuinely parsed, not a stub 404/`not_found`.
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
    # The structured `input` survives the wire as a mapping (blizzard#248 D1), never a
    # re-materialized JSON string.
    assert isinstance(bash_tool["input"], dict), f"tool input is not structured: {bash_tool!r}"

    # Provenance: the real commit sha, independently read off the bare origin, must
    # appear in the transcript's tool output — content only the real commit created.
    real_sha = _git_bare(origin_bare, "log", "--all", "--format=%H", "-1", "--", "transcript-proof.txt").strip()
    assert real_sha, "the mock harness's commit never reached the bare origin"
    assert real_sha[:7] in bash_tool["output"], (
        f"transcript tool output does not carry the real commit sha ({real_sha[:7]!r} not in {bash_tool['output']!r})"
    )
    assert _TRANSCRIPT_COMMIT_MESSAGE in bash_tool["output"]

    # The verdict landed as the final asst turn, carrying the judgement's own text.
    asst_turn = next(t for t in turns if t["kind"] == "asst")
    assert "pass" in asst_turn["text"]
    assert "the mock harness committed the change; checks are green" in asst_turn["text"]


def _worker_credential(config: RunnerConfig, lease_id: str) -> dict[str, str]:
    """The lease-token header a worker presents, minted here rather than intercepted: the
    plaintext handed to the spawn lives only in that process's environment, and
    ``record_lease_token`` is overwrite-safe by contract — a re-mint per lease id is what
    resume and takeover already do."""
    token, token_hash = LeaseToken.mint()
    engine = create_engine_from_url(config.db_url)
    try:
        SqlAlchemyRunnerStore(engine).record_lease_token(lease_id, token_hash, datetime.now(UTC))
    finally:
        engine.dispose()
    return {"X-Blizzard-Lease-Token": token}


def _graph_artifact_chunk_spec(work_ref: str) -> dict:
    """``mock_hub_chunk_spec`` plus one graph-scoped declaration — the phase 2b seed
    lever a real runner mints and pins into its own store before the node even starts."""
    spec = mock_hub_chunk_spec(work_ref)
    spec["graph_artifacts"] = [{"name": "docket", "kind": "asset", "content": "the docket text"}]
    return spec


def test_graph_scoped_artifact_reads_from_the_runners_own_pin_with_the_hub_unreachable(tmp_path: Path) -> None:
    """Proves against a real process boundary: the runner's own mirror of the seeded
    declaration answers ``--scope graph`` with the hub unreachable, while the node
    half — still hub-dependent — fails the same call would otherwise take."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/chunk", json=_graph_artifact_chunk_spec(_WORK_REF_URL))
        assert seeded.status_code == 201, seeded.text
        chunk_id = seeded.json()["chunk_id"]

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, host="127.0.0.1", port=_free_port())

        with _runner_api(config):
            runner_client = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=10.0)
            try:

                def _lease_minted() -> bool:
                    items = runner_client.get("/api/leases").json()["items"]
                    return any(item["chunk_id"] == chunk_id for item in items)

                minted = poll_until(lambda: _tick_then(config, fenced, _lease_minted), timeout=60.0)
                assert minted, "the chunk's first lease never minted"
                items = runner_client.get("/api/leases").json()["items"]
                lease_id = next(item["lease_id"] for item in items if item["chunk_id"] == chunk_id)
                worker = _worker_credential(config, lease_id)

                # No further ticks run past this point: the loop only advances when this
                # test calls it, so the lease found above stays active for every read below.
                assert hub.post("/_levers/unreachable", json={"remaining": 10_000}).status_code == 200

                listed = runner_client.get(
                    f"/api/leases/{lease_id}/artifacts", params={"scope": "graph"}, headers=worker
                )
                assert listed.status_code == 200, listed.text
                rows = listed.json()
                assert [r["name"] for r in rows] == ["docket"]
                assert rows[0]["scope"] == "graph" and rows[0]["content"] == "the docket text"

                fetched = runner_client.get(
                    f"/api/leases/{lease_id}/artifacts/docket", params={"scope": "graph"}, headers=worker
                )
                assert fetched.status_code == 200, fetched.text
                assert fetched.json()["content"] == "the docket text"

                # Contrast: the node half still proxies, so the same outage fails it — the
                # lever refuses at the application layer, hence its 503 passed through.
                node_read = runner_client.get(
                    f"/api/leases/{lease_id}/artifacts", params={"scope": "node"}, headers=worker
                )
                assert node_read.status_code == 503, node_read.text
                assert "unreachable" in node_read.json()["detail"]
            finally:
                runner_client.close()


#: The seq this scenario seeds its own hub-side segment under.
_SEEDED_SEGMENT_SEQ = 10_000


def test_a_closed_leases_transcript_resolves_to_the_hub_through_the_runner_api(tmp_path: Path) -> None:
    """All three of blizzard#249 D1's homes over ``build_hosted_app``'s real outbound
    wiring — local while open, the hub once closed (even with the local file rotated
    away), and the distinct hub-unreachable state once the hub process is gone."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    transcripts_root = tmp_path / "transcripts"
    fenced = _tick_env()
    fenced["BZ_TRANSCRIPTS_ROOT"] = str(transcripts_root)

    hub_port = _free_port()
    config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
    config = dataclasses.replace(config, host="127.0.0.1", port=_free_port(), transcripts_root=str(transcripts_root))

    # The runner daemon deliberately outlives the mock hub: the last leg reads the panel
    # route with the hub process already terminated.
    with _runner_api(config):
        panel = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=15.0)
        try:
            with mock_hub(bin_dir, hub_port) as hub:
                seeded = hub.post("/_seed/chunk", json=_transcript_chunk_spec(_WORK_REF_URL))
                assert seeded.status_code == 201, seeded.text
                chunk_id = seeded.json()["chunk_id"]

                open_read: dict = {}

                def _open_lease_answered() -> bool:
                    open_read.update(_panel_read_while_open(config, fenced, panel, chunk_id))
                    return bool(open_read.get("available") and open_read.get("turns"))

                assert poll_until(_open_lease_answered, timeout=90.0), "the open lease never served a transcript"
                assert open_read["provenance"] == "local", open_read
                assert open_read["hub_unreachable"] is False, open_read

                landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=120.0)
                assert landed, f"chunk did not land (status {_status(hub, chunk_id)!r})"
                lease = _sole_lease(panel, chunk_id)
                lease_id = lease["lease_id"]

                # Closed, hub reachable but holding nothing: a definite answer, so local.
                closed_local = panel.get(f"/api/leases/{lease_id}/transcript").json()
                assert closed_local["provenance"] == "local", closed_local
                assert closed_local["available"] is True, closed_local

                # Well past the runner's own lane: the mock retains nothing for an
                # at-or-under-mark seq, so a colliding seed would seed no turns at all.
                shipped = hub.post(
                    "/api/fleet/transcripts",
                    json={
                        "runner_id": config.runner_id,
                        "records": [
                            transcript_segment_record(
                                chunk_id,
                                seq=_SEEDED_SEGMENT_SEQ,
                                node_id=lease["node_id"],
                                epoch=lease["epoch"],
                                turns=[
                                    transcript_segment_turn(0, "asst", "from the hub archive"),
                                    transcript_segment_turn(1, "thinking", "and its reasoning"),
                                ],
                            )
                        ],
                    },
                )
                assert shipped.status_code == 200, shipped.text
                assert shipped.json()["applied"] == [_SEEDED_SEGMENT_SEQ], shipped.text

                archived = panel.get(f"/api/leases/{lease_id}/transcript").json()
                assert archived["provenance"] == "archived", archived
                # Every kind survives the trip since blizzard#248 widened the read model —
                # a thinking turn reaching the panel is what a narrowing read would lose.
                assert [t["text"] for t in archived["turns"]] == ["from the hub archive", "and its reasoning"], archived
                assert [t["kind"] for t in archived["turns"]] == ["asst", "thinking"], archived
                assert archived["hub_unreachable"] is False, archived

                # The rotation case (#249's own acceptance criterion): the local file is
                # gone and the panel is undegraded, because the hub still answers.
                shutil.rmtree(transcripts_root)
                rotated = panel.get(f"/api/leases/{lease_id}/transcript").json()
                assert rotated["provenance"] == "archived", rotated
                assert [t["text"] for t in rotated["turns"]] == ["from the hub archive", "and its reasoning"], rotated

            unreachable = panel.get(f"/api/leases/{lease_id}/transcript")
            assert unreachable.status_code == 200, unreachable.text
            body = unreachable.json()
            # The flag, not `reason`, is what the panel branches on — a failed local read
            # still reports its own `not_found`, which must never be the whole answer.
            assert body["hub_unreachable"] is True, body
            assert body["provenance"] == "local", body
            assert panel.get("/api/leases/no-such-lease/transcript").status_code == 404
        finally:
            panel.close()


def _sole_lease(panel: httpx.Client, chunk_id: str) -> dict:
    """The chunk's one lease as the runner's own lease list reports it."""
    leases = panel.get("/api/leases")
    assert leases.status_code == 200, leases.text
    matches = [item for item in leases.json()["items"] if item["chunk_id"] == chunk_id]
    assert len(matches) == 1, f"expected exactly one lease for the chunk, got {matches!r}"
    return matches[0]


def _panel_read_while_open(config: RunnerConfig, fenced: dict[str, str], panel: httpx.Client, chunk_id: str) -> dict:
    """One tick, then the panel's transcript read for the chunk's lease if it has one yet."""
    _drive(config, fenced, ticks=1, pause=0.3)
    matches = [item for item in panel.get("/api/leases").json()["items"] if item["chunk_id"] == chunk_id]
    if not matches:
        return {}
    resp = panel.get(f"/api/leases/{matches[0]['lease_id']}/transcript")
    return resp.json() if resp.status_code == 200 else {}


def _run_and_check(config: RunnerConfig, fenced: dict[str, str], hub: httpx.Client, chunk_id: str, target: str) -> bool:
    """One tick + a status read — the poll predicate the scenarios share."""
    _drive(config, fenced, ticks=1, pause=0.3)
    return _status(hub, chunk_id) == target


def _tick_then(config: RunnerConfig, fenced: dict[str, str], check) -> bool:
    """One tick, then evaluate ``check`` — the buffered-completion poll predicate."""
    _drive(config, fenced, ticks=1, pause=0.3)
    return bool(check())


# --- The runner's SSE stream (blizzard#317 Phase 2): no mock fleet needed, a bare
# migrated runtime dir serves the local API, and these tests drive the broker directly.


def _bare_runner_config(tmp_path: Path) -> RunnerConfig:
    """A migrated, otherwise-empty runner runtime — no workspace, no hub, no harness —
    enough to serve the local API and its stream route without ever ticking."""
    config = init_runner_environment(tmp_path / "runner")
    return dataclasses.replace(config, host="127.0.0.1", port=_free_port())


def test_runner_stream_delivers_live_and_replays_from_last_event_id(tmp_path: Path) -> None:
    """A live subscriber receives a published frame, and a reconnect with
    ``Last-Event-ID`` replays only what it missed — the runner's own route wiring,
    beside the shared core's generic keepalive proof (test_foundation_events.py)."""
    broker = EventBroker()
    config = _bare_runner_config(tmp_path)

    with _runner_api(config, events=broker):
        with sse_tap(config.port) as tap:
            first_id = broker.publish_lease_changed("ls_1", "ch_1", cause="created")
            second_id = broker.publish_lease_changed("ls_1", "ch_1", cause="transitioned")
            live = tap.collect(window=3.0)
        assert live.count("lease-changed") == 2, live

        # A reconnect naming the first event's id as its cursor must replay only the
        # second — dedup at the seam, not a second delivery of the first.
        with (
            httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=10.0) as client,
            client.stream("GET", "/api/events/stream", headers={"Last-Event-ID": str(first_id)}) as resp,
        ):
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            body = ""
            for chunk in resp.iter_text():
                body += chunk
                # the reserved comment's own trailing blank line, then the one
                # replayed frame's — two frames, so stop reading once both landed.
                if body.count("\n\n") >= 2:
                    break

        frames = parse_sse_frames(body)
        assert [f["event"] for f in frames] == ["lease-changed"], frames
        assert int(frames[0]["id"]) == second_id
        assert json.loads(frames[0]["data"])["cause"] == "transitioned"


def test_runner_stream_resumes_live_after_a_restart_reset_the_broker_ids(tmp_path: Path) -> None:
    """The restart shape a single-instance reconnect never presents: a **second** daemon
    behind the same port, its own broker minting ids from zero, resuming a cursor the first
    minted — the clamp's route wiring, its cursor a real header off the wire."""
    config = _bare_runner_config(tmp_path)

    first = EventBroker()
    with _runner_api(config, events=first), sse_tap(config.port) as tap:
        for cause in ("created", "transitioned", "released"):
            stale_cursor = first.publish_lease_changed("ls_1", "ch_1", cause=cause)
        assert tap.collect(window=3.0).count("lease-changed") == 3

    second = EventBroker()  # the restart: a fresh instance, its ids starting over
    with _runner_api(config, events=second), sse_tap(config.port, last_event_id=stale_cursor) as tap:
        live_id = second.publish_lease_changed("ls_2", "ch_2", cause="created")
        assert live_id < stale_cursor, "the fresh broker must mint an id below the stale cursor"
        assert tap.collect(window=5.0).count("lease-changed") == 1


def test_runner_stream_replays_a_restarted_brokers_buffered_tail_past_a_stale_cursor(tmp_path: Path) -> None:
    """Unlike the restart case above, the fresh broker here already holds buffered events
    *before* the reconnect — the ordinary case — so this exercises the replay-read half of
    the clamp, not just the live-dedup half."""
    config = _bare_runner_config(tmp_path)

    first = EventBroker()
    for cause in ("created", "transitioned", "released"):
        stale_cursor = first.publish_lease_changed("ls_1", "ch_1", cause=cause)

    second = EventBroker()  # the restart, already carrying buffered events before reconnect
    second.publish_lease_changed("ls_2", "ch_2", cause="created")
    second.publish_lease_changed("ls_2", "ch_2", cause="transitioned")
    assert second.latest_id() < stale_cursor, "the fresh broker's buffered ids must stay below the stale cursor"

    with _runner_api(config, events=second):
        tap = SseTap(f"http://127.0.0.1:{config.port}", last_event_id=stale_cursor)
        tap.start()
        try:
            assert tap.collect(window=5.0).count("lease-changed") == 2
        finally:
            tap.stop()


def test_events_stream_401s_without_a_session_over_tcp_under_oauth(tmp_path: Path) -> None:
    """``test_runner_route_gating.py`` proves this generically, in-process, over every
    human-lane route; this reproves it against a genuinely running daemon, whose
    hub-auth-mode probe hits a real listening stub, not an in-process double."""
    config = _bare_runner_config(tmp_path)

    jwks_app = FastAPI()

    @jwks_app.get("/api/auth/jwks.json")
    def _jwks() -> dict[str, list[object]]:
        return {"keys": []}

    jwks_port = _free_port()
    jwks_server = uvicorn.Server(uvicorn.Config(jwks_app, host="127.0.0.1", port=jwks_port, log_level="warning"))
    jwks_thread = threading.Thread(target=jwks_server.run, daemon=True)
    jwks_thread.start()
    jwks_client = httpx.Client(base_url=f"http://127.0.0.1:{jwks_port}", timeout=10.0)
    try:
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            with contextlib.suppress(httpx.HTTPError):
                if jwks_client.get("/api/auth/jwks.json").status_code == 200:
                    break
            time.sleep(0.1)
        else:
            raise AssertionError("the jwks stub never came up")

        app_config = dataclasses.replace(config, hub_url=f"http://127.0.0.1:{jwks_port}")
        with (
            _runner_api(app_config),
            httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=10.0) as client,
        ):
            resp = client.get("/api/events/stream")
        assert resp.status_code == 401, resp.text
    finally:
        jwks_client.close()
        jwks_server.should_exit = True
        jwks_thread.join(timeout=10.0)


def test_runner_sigterm_returns_promptly_with_a_client_parked_on_the_stream(tmp_path: Path) -> None:
    """SIGTERM sets ``app.state.shutdown`` synchronously (D1/D3): the process exits well
    inside uvicorn's graceful-drain bound with a client still connected, so the crash
    sweep's whole-process SIGTERM case finds the resume-marking ``finally`` unstranded."""
    runner_dir = tmp_path / "runner"
    runner_port = _free_port()
    runner_bin = str(Path(sys.executable).parent / "blizzard-runner")
    subprocess.run([runner_bin, "init", str(runner_dir)], check=True, capture_output=True, text=True)
    log = runner_dir / "daemon.log"
    proc = subprocess.Popen(
        [runner_bin, "host", "--dir", str(runner_dir), "--host", "127.0.0.1", "--port", str(runner_port)],
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{runner_port}", timeout=30.0)
    exit_code: int | None = None
    try:
        _await_http(proc, client, "/api/health", log=log)
        # The server dies mid-stream by design; closing our own end of a now-dead
        # connection is expected to raise, not a failure of what this test asserts.
        with contextlib.suppress(httpx.HTTPError), client.stream("GET", "/api/events/stream") as resp:
            assert resp.status_code == 200
            next(resp.iter_text())  # block for the reserved comment — the subscriber is live

            proc.send_signal(signal.SIGTERM)
            try:
                exit_code = proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
                raise AssertionError(
                    "runner did not exit within 2s of SIGTERM with a client parked on the stream "
                    "(uvicorn's own graceful-shutdown bound is 5s)"
                ) from None
        assert exit_code is not None
    finally:
        client.close()
        _terminate(proc)
