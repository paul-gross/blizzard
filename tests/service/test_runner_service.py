"""Runner service tier — the real runner against the mock hub (verification/blizzard.md).

The runner's loop is driven one tick at a time against a mock hub whose levers
manufacture rare states: unreachable hub, dropped ack, stale envelope — plus two scenarios
reading a real transcript back through the runner's own local HTTP API (#29, blizzard#249).
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import time
from pathlib import Path

import httpx
import pytest

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop.build import LoopWiring
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
    transcript_segment_record,
    transcript_segment_turn,
)

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
