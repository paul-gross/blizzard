"""Resume-time spawn-preamble elision end to end — scenario 12 of the standing e2e smoke — issue #149.

The full-stack proof that a **resumed** node-entry spawn is actually handed less than a
fresh one, and is told when its standing instructions moved. Everything below the unit and
component tiers stubs the harness seam: the component tier asserts the ``prompt_prefix``
the loop *hands* a fake adapter, which proves the wiring but not that a real harness
process on the other side of a real ``claude --resume`` receives it. This scenario closes
that gap on the real forge + hub + runner + ``mock-claude-code`` rails.

**What makes it observable.** The mock harness records each turn's *user* text into a
Claude-Code-shaped transcript (``<BZ_TRANSCRIPTS_ROOT>/mock-claude-code/<session_id>.jsonl``),
appended across spawn and every later ``--resume`` by separate processes. For an untagged
prompt that user text **is the runner's preamble verbatim**
(``blizzard-mock:harness/engine.py`` — ``prose = tagged.prose if tagged is not None else
preamble``). So one session's transcript is the ordered record of what each of its turns
was actually sent, which is precisely the thing under test. No mock change is needed, and
nothing here reaches around the seam: it reads what the harness received.

The graph is the sibling ``test_session_modes_e2e`` shape — ``build`` is
``session: resume:build``, ``review`` is ``session: fresh``, and a scripted review fails
once then passes — because that is exactly what enters ``build`` twice on **one** session.
Build's transcript therefore carries a fresh spawn, a judgement resume, and then a
*resumed node-entry spawn*: the turn this issue changes.

Two scenarios, one per half of the issue:

* **the efficiency half** — nothing changed between the two entries, so the second spawn
  collapses both standing layers to one line and re-sends neither;
* **the correctness half** — an operator replaces the workspace prompt through the real
  ``PUT /api/workspace-prompt`` door *between* the two entries, and the second spawn
  carries the new prose behind an explicit updated-since-your-previous-turn announcement.

Skipped unless ``BLIZZARD_E2E=1`` with the sibling ``blizzard-mock`` worktree provisioned.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.harness.preamble import (
    RESUME_BLIZZARD_UNCHANGED,
    RESUME_STANDING_UNCHANGED,
    RESUME_UPDATED_NOTICE,
)
from blizzard.runner.loop.build import run_single_tick
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from tests.e2e.test_acceptance_loop import (
    FIXTURE_ENV,
    REPO,
    REPO_NAME,
    _forge,
    _free_port,
    _hub,
    _mock_bin_dir,
    _runner_api,
    _runner_config,
    _winter_source,
)
from tests.e2e.test_session_modes_e2e import _graph_yaml

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e resume preamble needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

#: The two standing layers. Each carries a distinctive sentinel so its presence or absence
#: in a transcript turn is unambiguous, and each is padded to a **realistic** size.
#:
#: The padding is load-bearing, not decoration. The elision replaces two layers with one
#: banner of real prose (~450 chars), so whether it saves anything at all depends on what
#: it replaced: against one-line sentinels the "saving" is negative. A deployment's actual
#: layer 1 is the packaged preamble (~2.5 KB) or an operator's own framing, and layer 2 is
#: a workspace policy document — the shape modelled here. Asserting the saving against
#: toy-sized prose would assert something untrue of every real deployment and false here.
_RUNNER_PROMPT = (
    "LAYER-ONE-BLIZZARD-FRAMING-SENTINEL\n\n"
    "You are a worker in a blizzard fleet: an autonomous fleet-management system that claims\n"
    "units of work off a queue and drives each through a graph of nodes. You are one step in\n"
    "that graph, and the runner that spawned you holds your lease. Your interface to the fleet\n"
    "is the `blizzard` CLI, already on your PATH; the worker-facing verbs are `ask`,\n"
    "`work-items`, `artifact list`, `artifact get`, and `artifact create`. The operator verbs\n"
    "the full help also lists are not yours to run.\n"
)
_WORKSPACE_PROMPT = (
    "LAYER-TWO-WORKSPACE-POLICY-SENTINEL\n\n"
    "Work only inside your assigned feature environment's worktrees, named in the facts table\n"
    "below. Before a fresh build, reset to a known-good baseline: bring services down, check\n"
    "every worktree out to the base branch, destroy and re-provision resources, and only then\n"
    "start services. Never exercise an environment you have not provisioned. If the checkout\n"
    "step fails for any reason, stop immediately and report rather than hand-repairing.\n"
)
_REPLACEMENT_PROMPT = (
    "LAYER-TWO-REPLACED-POLICY-SENTINEL\n\n"
    "The delivery policy has changed mid-chunk: do not open pull requests directly. Push your\n"
    "branch and declare it, then let the fleet's deliver node open and land the PR on your\n"
    "behalf. Everything else in the previous policy still stands unless restated here.\n"
)


def _user_turns(transcripts_root: Path, session_id: str) -> list[str]:
    """Every ``user`` record's text for one session, in turn order.

    The mock appends one per turn; for an untagged prompt a *spawn* turn's text is the
    runner's preamble verbatim, and a resume-with-message turn's is the resume message (or
    the mock's synthetic placeholder when the message carries no prose).
    """
    path = transcripts_root / "mock-claude-code" / f"{session_id}.jsonl"
    assert path.is_file(), f"no mock transcript at {path}"
    turns: list[str] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("type") != "user":
            continue
        message = record.get("message", record)
        content = message.get("content", "")
        turns.append(content if isinstance(content, str) else json.dumps(content))
    return turns


def _spawn_turns(turns: list[str]) -> list[str]:
    """The turns that are *spawn* preambles — the ones carrying the facts table.

    Layer 3 is unconditional on every spawn path, so its header is the discriminator that
    separates a spawn turn from a resume-with-message turn (a judgement elicitation or a
    nudge), which carries no preamble at all.
    """
    return [turn for turn in turns if "| Field | Value |" in turn]


def _build_sessions(db_url: str, chunk_id: str) -> list[str]:
    """The ``session_id`` of each ``build`` node-step lease, in mint order."""
    store = SqlAlchemyRunnerStore(create_engine_from_url(db_url))
    leases = [store.lease(lid) for lid in store.lease_ids_for_chunk(chunk_id)]
    ordered = sorted((lz for lz in leases if lz is not None), key=lambda lz: lz.created_at)
    return [lz.session_id for lz in ordered if lz.node_name == "build" and lz.session_id is not None]


@contextlib.contextmanager
def _live_stack(tmp_path: Path):  # type: ignore[no-untyped-def]
    """The shared scaffolding both scenarios below stand on.

    Yields ``(hub, chunk_id, config, workspace, origin_bare, fenced_env)`` with the graph
    published, an issue ingested and promoted, and a runner configured with both standing
    preamble layers set to their sentinels.
    """
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
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "resume preamble", "body": "the chunk"})
        assert issue.status_code == 201, issue.text
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        # Both standing layers set: this scenario is about what happens to them, so a
        # deployment with an empty layer 2 would prove only half of it.
        config = dataclasses.replace(
            config,
            max_agents=1,
            runner_prompt=_RUNNER_PROMPT,
            workspace_prompt=_WORKSPACE_PROMPT,
        )
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        yield hub, chunk_id, config, workspace, origins / f"{REPO_NAME}.git", fenced


def _drive(config, hub, chunk_id, fenced_env, *, on_tick=None, timeout: float = 180.0) -> str:  # type: ignore[no-untyped-def]
    """Tick until terminal, calling ``on_tick(runner_client)`` after each pass.

    A local variant of :func:`~tests.e2e.test_acceptance_loop._drive_until_done` that hands
    the caller the runner's own local-API client between ticks — the seam an operator
    action (here ``PUT /api/workspace-prompt``) has to arrive through to be a real test of
    the live door rather than a store poke behind it.
    """
    prior = dict(os.environ)
    os.environ.update(fenced_env)
    try:
        with _runner_api(config):
            runner = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=10.0)
            try:
                deadline = time.monotonic() + timeout
                status = "ready"
                while time.monotonic() < deadline:
                    run_single_tick(config)
                    if on_tick is not None:
                        on_tick(runner)
                    detail = hub.get(f"/api/chunks/{chunk_id}")
                    assert detail.status_code == 200, detail.text
                    status = detail.json()["status"]
                    if status in {"done", "stopped", "needs_human"}:
                        return status
                    time.sleep(0.5)
                return status
            finally:
                runner.close()
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _transcripts_root(config, workspace: Path) -> Path:  # type: ignore[no-untyped-def]
    """Where the mock wrote its transcripts for this run.

    The mock resolves ``BZ_TRANSCRIPTS_ROOT`` when set and otherwise falls back **under the
    fence**, beside the session-state directory — never a real home. Mirrored here rather
    than assumed, so the scenario reads the same place the writer wrote.
    """
    override = os.environ.get("BZ_TRANSCRIPTS_ROOT") or config.transcripts_root
    return Path(override) if override else workspace / ".blizzard-mock-harness" / "transcripts"


def _assert_entered_build_twice(origin_bare: Path, build_sessions: list[str]) -> str:
    """The shared precondition: build really was entered twice, on one session."""
    build_md = subprocess.run(
        ["git", "--git-dir", str(origin_bare), "show", "main:BUILD.md"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert build_md.count("build pass") == 2, f"expected two build passes on main, got:\n{build_md}"
    assert len(build_sessions) == 2, f"build should have two node-step leases, got {build_sessions}"
    assert len(set(build_sessions)) == 1, f"build's re-entry did not resume its own session: {build_sessions}"
    return build_sessions[0]


def test_resumed_node_entry_elides_unchanged_standing_layers(tmp_path: Path) -> None:
    """The efficiency half, at runtime: nothing moved, so the second entry sends neither
    standing layer — and still sends its own facts table in full."""
    with _live_stack(tmp_path) as (hub, chunk_id, config, workspace, origin_bare, fenced):
        status = _drive(config, hub, chunk_id, fenced)
        assert status == "done", f"chunk did not reach done (last status {status!r})"
        db_url = config.db_url

    build_session = _assert_entered_build_twice(origin_bare, _build_sessions(db_url, chunk_id))
    spawns = _spawn_turns(_user_turns(_transcripts_root(config, workspace), build_session))
    assert len(spawns) == 2, f"expected two spawn turns on build's session, got {len(spawns)}"
    first, second = spawns

    # The fresh spawn is unchanged by this issue: both standing layers in full (AC4).
    assert _RUNNER_PROMPT in first
    assert _WORKSPACE_PROMPT in first
    assert RESUME_STANDING_UNCHANGED not in first
    assert RESUME_UPDATED_NOTICE not in first

    # The resumed node-entry spawn: one line in place of both layers, neither re-sent.
    assert RESUME_STANDING_UNCHANGED in second, (
        f"resumed spawn did not collapse its standing layers; it began:\n{second[:400]}"
    )
    assert _RUNNER_PROMPT not in second, "layer 1 was re-sent to a session that already held it"
    assert _WORKSPACE_PROMPT not in second, "layer 2 was re-sent to a session that already held it"
    assert RESUME_UPDATED_NOTICE not in second, "an update was announced when nothing changed"

    # AC6 at runtime: layer 3 is unconditional, and carries THIS attempt's lease — a stale
    # table surviving into a resumed spawn is the hazard, so the prior lease must be absent.
    store = SqlAlchemyRunnerStore(create_engine_from_url(db_url))
    leases = [store.lease(lid) for lid in store.lease_ids_for_chunk(chunk_id)]
    build_leases = sorted(
        (lz for lz in leases if lz is not None and lz.node_name == "build"), key=lambda lz: lz.created_at
    )
    assert f"| lease id | `{build_leases[1].lease_id}` |" in second
    assert build_leases[0].lease_id not in second, "the resumed spawn carried the PREVIOUS attempt's lease id"

    # The elision is a real saving against realistically-sized standing prose (see the
    # sentinel constants: the banner is real prose, so this only holds once what it
    # replaces is real too).
    assert len(second) < len(first), f"resumed prefix ({len(second)}) was not shorter than fresh ({len(first)})"


def test_resumed_node_entry_announces_a_replaced_workspace_prompt(tmp_path: Path) -> None:
    """The correctness half, at runtime: an operator replaces the workspace prompt through
    the live ``PUT /api/workspace-prompt`` door between build's two entries, and the second
    entry is told, rather than being handed the new prose where the old one sat."""
    replaced: list[bool] = []

    with _live_stack(tmp_path) as (hub, chunk_id, config, workspace, origin_bare, fenced):
        db_url = config.db_url

        def replace_once(runner: httpx.Client) -> None:
            # Fire as soon as build has exited and review holds the chunk — that is
            # strictly between build's first and second entries, which is the window this
            # issue exists to make safe. Idempotent: only the first crossing writes.
            if replaced:
                return
            store = SqlAlchemyRunnerStore(create_engine_from_url(db_url))
            nodes = {
                lz.node_name for lid in store.lease_ids_for_chunk(chunk_id) if (lz := store.lease(lid)) is not None
            }
            if "review" not in nodes:
                return
            response = runner.put("/api/workspace-prompt", json={"prompt": _REPLACEMENT_PROMPT})
            assert response.status_code == 200, response.text
            replaced.append(True)

        status = _drive(config, hub, chunk_id, fenced, on_tick=replace_once)
        assert status == "done", f"chunk did not reach done (last status {status!r})"

    assert replaced, "the workspace-prompt replace never fired — the scenario proves nothing"
    build_session = _assert_entered_build_twice(origin_bare, _build_sessions(db_url, chunk_id))
    spawns = _spawn_turns(_user_turns(_transcripts_root(config, workspace), build_session))
    assert len(spawns) == 2, f"expected two spawn turns on build's session, got {len(spawns)}"
    first, second = spawns

    assert _WORKSPACE_PROMPT in first, "the fresh spawn should carry the ORIGINAL workspace prose"

    # The announcement leads, the new prose follows in full, the superseded prose is gone.
    assert second.startswith(RESUME_UPDATED_NOTICE), (
        f"a replaced workspace prompt arrived unannounced; the spawn began:\n{second[:400]}"
    )
    assert _REPLACEMENT_PROMPT in second, "the replacement prose did not reach the resumed worker"
    assert _WORKSPACE_PROMPT not in second, "the superseded prose was re-sent alongside its replacement"

    # Layer 1 did not move, so it stays collapsed — and its collapse line is what still
    # introduces the facts table, the per-layer rule this branch is the reason for.
    assert RESUME_BLIZZARD_UNCHANGED in second
    assert _RUNNER_PROMPT not in second
    assert "| Field | Value |" in second
