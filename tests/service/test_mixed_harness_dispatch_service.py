"""Mixed-harness dispatch, service tier: one real runner, wired with BOTH Claude Code and
OpenCode binaries, driven against a real ``blizzard-mock-hub`` subprocess.

Every operation the real dispatch loop performs — peek, preference
resolution, a bare node's default fallthrough, two live lineages sharing one
``RunnerConfig`` — is proven through the loop's own real steps, never a hand-built call."""

from __future__ import annotations

import dataclasses
import os
import time
from pathlib import Path

import httpx2
import pytest

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.observability.analytics.events import KIND_AGENT_SPAWN, KIND_SKILL_INVOCATION
from blizzard.hub.domain.observability.analytics.extraction import extract_events
from blizzard.hub.domain.observability.transcripts import TranscriptTurn
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop_wiring import LoopWiring
from blizzard.runner.store.schema import leases, transcript_segments, usage_facts
from blizzard.wire.transcript_segment import TurnSegmentView
from tests.fleet_launchers import runner_api, runner_config
from tests.fleet_world import REPO_NAME, mint_world, scripted_build_prompt
from tests.runner_fakes import daemon_store
from tests.runner_join import fleet_headers
from tests.service.support import (
    BUILD_SCRIPT,
    JUDGEMENT_SCRIPT,
    OPENCODE_BUILD_SCRIPT,
    mock_hub,
    mock_hub_chunk_spec,
    poll_until,
    require_mock_fleet,
    service_gate,
)
from tests.support import free_port

pytestmark = [pytest.mark.service, service_gate]

_WORK_REF_URL = "blizzard/toy-api/issues/1"

#: A harness id `runner_config` never wires, so this name is guaranteed unresolvable.
_UNBOUND_HARNESS = "special-harness"


def _tick_env() -> dict[str, str]:
    fenced = dict(os.environ)
    fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
    return fenced


def _drive(config: RunnerConfig, fenced: dict[str, str], *, ticks: int, pause: float = 0.5) -> None:
    # Not a test function, so the fixture-injected `monkeypatch` can't reach here —
    # `MonkeyPatch`'s own standalone context-manager form, scoped to this call.
    with pytest.MonkeyPatch.context() as mp:
        for key, value in fenced.items():
            mp.setenv(key, value)
        for _ in range(ticks):
            LoopWiring.of(config).tick_once()
            time.sleep(pause)


def _status(hub: httpx2.Client, chunk_id: str) -> str:
    return hub.get(f"/api/fleet/chunks/{chunk_id}", headers=fleet_headers(hub)).json()["status"]


def _run_and_check(
    config: RunnerConfig, fenced: dict[str, str], hub: httpx2.Client, chunk_id: str, target: str
) -> bool:
    _drive(config, fenced, ticks=1, pause=0.3)
    return _status(hub, chunk_id) == target


def _tick_then(config: RunnerConfig, fenced: dict[str, str], check) -> bool:
    _drive(config, fenced, ticks=1, pause=0.3)
    return bool(check())


def _seed(hub: httpx2.Client, spec: dict) -> str:
    resp = hub.post("/_seed/chunk", json=spec)
    assert resp.status_code == 201, resp.text
    return resp.json()["chunk_id"]


def _leases_for_chunk(config: RunnerConfig, chunk_id: str) -> list[dict]:
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    daemon_store(engine).connections.select(leases).where(leases.c.chunk_id == chunk_id)
                ).mappings()
            ]
    finally:
        engine.dispose()


def _segment_spawn_cwds(config: RunnerConfig, chunk_id: str) -> list[str | None]:
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.connect() as conn:
            query = (
                daemon_store(engine)
                .connections.select(transcript_segments, transcript_segments.c.spawn_cwd)
                .where(transcript_segments.c.chunk_id == chunk_id)
            )
            return [row.spawn_cwd for row in conn.execute(query)]
    finally:
        engine.dispose()


def _usage_facts_for_chunk(config: RunnerConfig, chunk_id: str) -> list[dict]:
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.connect() as conn:
            query = daemon_store(engine).connections.select(usage_facts).where(usage_facts.c.chunk_id == chunk_id)
            return [dict(row) for row in conn.execute(query).mappings()]
    finally:
        engine.dispose()


def _lease_id_for_chunk(config: RunnerConfig, chunk_id: str) -> str | None:
    rows = _leases_for_chunk(config, chunk_id)
    return rows[0]["lease_id"] if rows else None


# 1. Capability-matched dispatch: the matched peek offers only an entry this runner's reported
# capabilities can work and claim.


def _incompatible_chunk_spec(work_ref: str) -> dict:
    """`mock_hub_chunk_spec`'s own build node, walled off behind a harness id neither
    of this runner's two wired binaries ever binds."""
    spec = mock_hub_chunk_spec(work_ref)
    spec["nodes"]["build"]["session_harnesses"] = [_UNBOUND_HARNESS]
    return spec


def test_under_strict_an_incompatible_head_starves_the_workable_entry_behind_it_until_it_clears(
    tmp_path: Path,
) -> None:
    """Under `[queue] strict` an incompatible chunk queued ahead of a workable one holds the
    matched peek empty on every tick, starving the workable entry; stopping the incompatible
    one lets the same runner loop reach and land the workable one."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        incompatible_id = _seed(hub, _incompatible_chunk_spec(_WORK_REF_URL))  # seeded first — the peek's head
        workable_id = _seed(hub, mock_hub_chunk_spec(_WORK_REF_URL))
        config = dataclasses.replace(
            runner_config(tmp_path / "runner", workspace, bin_dir, hub_port), queue_strict=True
        )

        _drive(config, fenced, ticks=5, pause=0.3)

        # Never offered — never claimed, never escalated (nothing was ever leased).
        assert _status(hub, incompatible_id) == "ready"
        assert _lease_id_for_chunk(config, incompatible_id) is None
        # Starved behind it — the runner never even reached this one.
        assert _status(hub, workable_id) == "ready"
        assert _lease_id_for_chunk(config, workable_id) is None

        assert hub.post("/_seed/stop", json={"chunk_id": incompatible_id}).status_code == 200
        landed = poll_until(lambda: _run_and_check(config, fenced, hub, workable_id, "done"), timeout=90.0)
        assert landed, f"the workable entry never landed once unblocked (status {_status(hub, workable_id)!r})"


def test_by_default_the_matched_peek_passes_over_an_incompatible_head_to_the_workable_entry(tmp_path: Path) -> None:
    """Without `[queue] strict` the same queue never starves: the matched peek passes over the
    incompatible head, so the runner loop lands the workable entry behind it and never leases
    the incompatible one."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        incompatible_id = _seed(hub, _incompatible_chunk_spec(_WORK_REF_URL))  # seeded first — the queue's head
        workable_id = _seed(hub, mock_hub_chunk_spec(_WORK_REF_URL))
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        landed = poll_until(lambda: _run_and_check(config, fenced, hub, workable_id, "done"), timeout=90.0)
        assert landed, (
            f"the workable entry behind an incompatible head never landed (status {_status(hub, workable_id)!r})"
        )
        assert _status(hub, incompatible_id) == "ready"
        assert _lease_id_for_chunk(config, incompatible_id) is None


# 2. Conflicting harness preference order — resolved deterministically against what
# this runner actually has configured.


def _preference_chunk_spec(work_ref: str, *, harnesses: list[str], build_script: str) -> dict:
    spec = mock_hub_chunk_spec(work_ref)
    spec["nodes"]["build"]["session_harnesses"] = harnesses
    spec["nodes"]["build"]["prompt"] = build_script
    return spec


def test_preference_order_resolves_the_first_available_member_over_the_configured_default(tmp_path: Path) -> None:
    """``opencode`` listed ahead of ``claude_code`` — both bound on this runner — wins the
    fresh mint even though ``claude_code`` is this runner's own registry-order default."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        chunk_id = _seed(
            hub,
            _preference_chunk_spec(
                _WORK_REF_URL, harnesses=["opencode", "claude_code"], build_script=OPENCODE_BUILD_SCRIPT
            ),
        )
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
        assert landed, f"chunk did not land (status {_status(hub, chunk_id)!r})"
        harness_ids = {row["harness_id"] for row in _leases_for_chunk(config, chunk_id)}
        assert harness_ids == {"opencode"}, harness_ids


def test_preference_order_skips_an_unresolvable_member_and_falls_through_to_the_next(tmp_path: Path) -> None:
    """A first-listed member this runner never binds is skipped — recorded, never fatal —
    and the fresh mint falls through to the next member it actually has configured."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        chunk_id = _seed(
            hub,
            _preference_chunk_spec(
                _WORK_REF_URL, harnesses=[_UNBOUND_HARNESS, "claude_code"], build_script=BUILD_SCRIPT
            ),
        )
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
        assert landed, f"chunk did not land (status {_status(hub, chunk_id)!r})"
        harness_ids = {row["harness_id"] for row in _leases_for_chunk(config, chunk_id)}
        assert harness_ids == {"claude_code"}, harness_ids


def test_a_native_only_multi_harness_session_is_matched_by_the_reported_supported_models(tmp_path: Path) -> None:
    """A strict session naming only native models across both harnesses has no tier to match: the matched peek
    offers it only because each binding reports the native names it supports, and the runner then resolves the
    one OpenCode accepts."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        spec = _preference_chunk_spec(
            _WORK_REF_URL, harnesses=["opencode", "claude_code"], build_script=OPENCODE_BUILD_SCRIPT
        )
        spec["nodes"]["build"]["session_model"] = ["opus-unreported", "openai/gpt-5"]
        chunk_id = _seed(hub, spec)
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
        assert landed, f"chunk did not land (status {_status(hub, chunk_id)!r})"
        harness_ids = {row["harness_id"] for row in _leases_for_chunk(config, chunk_id)}
        assert harness_ids == {"opencode"}, harness_ids


# --------------------------------------------------------------------------- #
# 3. A bare lineage resolving through the default.


def test_a_bare_node_with_no_declared_preference_resolves_through_this_runners_configured_default(
    tmp_path: Path,
) -> None:
    """`mock_hub_chunk_spec`'s own build node declares no `session_harnesses` and the
    chunk itself declares no `default_harnesses` — the absolute bare-node case
    (`Spawner.spawn`'s own no-`session_harnesses` fallback to `default_harness_id`)."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        chunk_id = _seed(hub, mock_hub_chunk_spec(_WORK_REF_URL))
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
        assert landed, f"chunk did not land (status {_status(hub, chunk_id)!r})"
        harness_ids = {row["harness_id"] for row in _leases_for_chunk(config, chunk_id)}
        assert harness_ids == {"claude_code"}, harness_ids


def test_a_bare_node_still_honors_the_chunks_own_declared_default(tmp_path: Path) -> None:
    """A bare node's eligibility reads the chunk's own declared `default_harnesses`, proven
    both ways under `[queue] strict`: never offered and starved behind an unbound default, then
    claimed once a compatible chunk's own default takes its place."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        incompatible_spec = mock_hub_chunk_spec(_WORK_REF_URL)
        incompatible_spec["default_harnesses"] = [_UNBOUND_HARNESS]  # the node's own field stays empty
        compatible_spec = mock_hub_chunk_spec(_WORK_REF_URL)
        compatible_spec["default_harnesses"] = ["opencode"]  # the node's own field stays empty
        incompatible_id = _seed(hub, incompatible_spec)  # seeded first — the peek's head
        compatible_id = _seed(hub, compatible_spec)
        config = dataclasses.replace(
            runner_config(tmp_path / "runner", workspace, bin_dir, hub_port), queue_strict=True
        )

        _drive(config, fenced, ticks=5, pause=0.3)

        # Never offered: a peek ignoring the chunk's own default would hand it out.
        assert _status(hub, incompatible_id) == "ready"
        assert _lease_id_for_chunk(config, incompatible_id) is None
        # Starved behind it — the runner never even reached the compatible entry.
        assert _status(hub, compatible_id) == "ready"
        assert _lease_id_for_chunk(config, compatible_id) is None

        assert hub.post("/_seed/stop", json={"chunk_id": incompatible_id}).status_code == 200
        # Once unblocked: compatible at CLAIM (this runner binds opencode) — claimed and
        # run to done, the chunk's OWN declared preference, not merely "any default".
        landed = poll_until(lambda: _run_and_check(config, fenced, hub, compatible_id, "done"), timeout=90.0)
        assert landed, f"chunk did not land (status {_status(hub, compatible_id)!r})"
        harness_ids = {row["harness_id"] for row in _leases_for_chunk(config, compatible_id)}
        # Mock-double limitation: `blizzard-mock-hub`'s `envelope()` never bakes the chunk's
        # default onto the node, so SPAWN falls through to this runner's own default instead.
        assert harness_ids == {"claude_code"}, harness_ids


# --------------------------------------------------------------------------- #
# 4. Two declared session lineages, live together on one RunnerConfig.

_CLAUDE_SKILL_BUILD_SCRIPT = scripted_build_prompt(
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    # skill_invocation via `_CLAUDE_CODE_JSONL_2` in src/blizzard/hub/domain/observability/analytics/dialects.py.
    "tool_call('Skill', {'skill': 'wf-commit'}, output='ran the commit skill')\n"
    '(pathlib.Path(repo) / "LANDED.md").write_text("landed by the mock harness\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    'subprocess.run(["git", "-C", repo, "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",'
    ' "commit", "-m", "feat: land a change from the mock harness"], check=True)\n'
    "_branch = subprocess.run(\n"
    '    ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    "_commit = subprocess.run(\n"
    '    ["git", "-C", repo, "rev-parse", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    'subprocess.run(["git", "-C", repo, "push", "origin", _branch], check=True)\n'
    "subprocess.run(\n"
    '    ["blizzard", "runner", "artifact", "commit",\n'
    '     "--repo", repo, "--branch", _branch, "--commit", _commit],\n'
    "    check=True,\n"
    ")\n"
)

_OPENCODE_TASK_BUILD_SCRIPT = scripted_build_prompt(
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    # agent_spawn via `_OPENCODE_EXPORT_1` in src/blizzard/hub/domain/observability/analytics/dialects.py.
    "tool_call('task', {'subagent_type': 'reviewer'}, output='spawned a sub-agent')\n"
    '(pathlib.Path(repo) / "LANDED.md").write_text("landed by the mock harness\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True, capture_output=True)\n'
    'subprocess.run(["git", "-C", repo, "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",'
    ' "commit", "-m", "feat: land a change from the mock harness"], check=True, capture_output=True)\n'
    "_branch = subprocess.run(\n"
    '    ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    "_commit = subprocess.run(\n"
    '    ["git", "-C", repo, "rev-parse", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    'subprocess.run(["git", "-C", repo, "push", "origin", _branch], check=True, capture_output=True)\n'
    "subprocess.run(\n"
    '    ["blizzard", "runner", "artifact", "commit",\n'
    '     "--repo", repo, "--branch", _branch, "--commit", _commit],\n'
    "    check=True, capture_output=True,\n"
    ")\n"
)


def _claude_skill_chunk_spec(work_ref: str) -> dict:
    spec = mock_hub_chunk_spec(work_ref)
    spec["nodes"]["build"]["prompt"] = _CLAUDE_SKILL_BUILD_SCRIPT
    return spec


def _opencode_task_chunk_spec(work_ref: str) -> dict:
    return {
        "graph_id": "gr_mixed_dispatch_opencode",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "session": "resume",
                "session_harnesses": ["opencode"],
                "judged_by": "worker",
                "prompt": _OPENCODE_TASK_BUILD_SCRIPT,
                "judgement_prompt": JUDGEMENT_SCRIPT,
                "choices": [{"name": "pass", "description": "committed and green", "to": "deliver"}],
                "retries_max": 1,
            },
            "deliver": {
                "executor": "hub",
                "judgement": {
                    "choices": {
                        "landed": {"description": "Every repo merged cleanly.", "to": "done"},
                        "conflict": {"description": "A repo did not merge cleanly.", "to": "build"},
                        "failure": {"description": "A repo failed to land.", "to": "build"},
                    },
                },
            },
        },
        "work_refs": [{"source": "mock", "ref": work_ref}],
    }


def _segment_turns(runner_client: httpx2.Client, chunk_id: str, segments: list[dict]) -> list[dict]:
    turns: list[dict] = []
    for segment in segments:
        content = runner_client.get(f"/api/chunks/{chunk_id}/transcripts/{segment['segment_id']}")
        assert content.status_code == 200, content.text
        turns.extend(content.json()["turns"])
    return turns


def test_two_session_lineages_interleave_on_one_dispatch_loop_with_no_cross_talk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One Claude Code and one OpenCode lineage, driven to done by ONE runner config's
    tick loop, proving no cross-talk at any seam: spawn, completion, usage, transcript
    segments, and analytics provenance."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    transcripts_root = tmp_path / "transcripts"
    fenced = _tick_env()
    fenced["BZ_TRANSCRIPTS_ROOT"] = str(transcripts_root)

    hub_port = free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        claude_id = _seed(hub, _claude_skill_chunk_spec(_WORK_REF_URL))
        opencode_id = _seed(hub, _opencode_task_chunk_spec(_WORK_REF_URL))

        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(
            config,
            host="127.0.0.1",
            port=free_port(),
            transcripts_root=str(transcripts_root),
            transcripts_ship=True,
        )

        # The live opencode export below needs BZ_TRANSCRIPTS_ROOT at call time, not
        # merely at spawn time, so the fenced env stays applied for this whole block.
        for key, value in fenced.items():
            monkeypatch.setenv(key, value)
        with runner_api(config):
            both_landed = poll_until(
                lambda: _tick_then(
                    config,
                    fenced,
                    lambda: _status(hub, claude_id) == "done" and _status(hub, opencode_id) == "done",
                ),
                timeout=150.0,
            )
            assert both_landed, (
                f"both lineages never landed (claude {_status(hub, claude_id)!r}, "
                f"opencode {_status(hub, opencode_id)!r})"
            )

            # --- spawn + completion + harness provenance, per lineage — never crossed ---
            claude_leases = _leases_for_chunk(config, claude_id)
            opencode_leases = _leases_for_chunk(config, opencode_id)
            assert claude_leases and opencode_leases
            assert {row["harness_id"] for row in claude_leases} == {"claude_code"}
            assert {row["harness_id"] for row in opencode_leases} == {"opencode"}
            claude_lease_ids = {row["lease_id"] for row in claude_leases}
            opencode_lease_ids = {row["lease_id"] for row in opencode_leases}
            assert claude_lease_ids.isdisjoint(opencode_lease_ids)

            # --- every segment froze its own spawn's working directory, per lineage ---
            for chunk_id in (claude_id, opencode_id):
                cwds = _segment_spawn_cwds(config, chunk_id)
                assert cwds, chunk_id
                assert all(cwds), cwds
                assert config.home_workspace.root
                # The basic provider spawns into the chunk's own environment, named after the chunk.
                assert set(cwds) == {str(Path(config.home_workspace.root) / chunk_id)}, cwds

            # --- usage recorded (judgement resolved into a completion means both a spawn
            # and a judge invocation earned their own facts), per lineage — never crossed ---
            claude_usage = _usage_facts_for_chunk(config, claude_id)
            opencode_usage = _usage_facts_for_chunk(config, opencode_id)
            assert {u["kind"] for u in claude_usage} == {"spawn", "judge"}, claude_usage
            assert {u["kind"] for u in opencode_usage} == {"spawn", "judge"}, opencode_usage
            assert {u["lease_id"] for u in claude_usage} <= claude_lease_ids
            assert {u["lease_id"] for u in opencode_usage} <= opencode_lease_ids

            # --- transcript pumping produced segments, correctly attributed, per lineage ---
            runner_client = httpx2.Client(base_url=config.local_api_url, timeout=15.0)
            try:
                claude_segments = runner_client.get(f"/api/chunks/{claude_id}/transcripts").json()["segments"]
                opencode_segments = runner_client.get(f"/api/chunks/{opencode_id}/transcripts").json()["segments"]
                assert claude_segments, "expected at least one claude_code segment"
                assert not any("spawn_cwd" in s for s in claude_segments + opencode_segments)
                assert opencode_segments, "expected at least one opencode segment"
                assert all(s["harness_id"] == "claude_code" for s in claude_segments), claude_segments
                assert all(s["harness_id"] == "opencode" for s in opencode_segments), opencode_segments
                assert all(s["normalizer_version"] == "claude-code-jsonl/2" for s in claude_segments), claude_segments
                assert all(s["normalizer_version"] == "opencode-export/1" for s in opencode_segments), opencode_segments
                # harness_version is read from the transcript itself, per dialect; the mock's
                # claude_code writer never emits one, so that side is genuinely always None.
                assert all(s["harness_version"] is None for s in claude_segments), claude_segments
                assert all(s["harness_version"] for s in opencode_segments), opencode_segments

                # --- analytics derivation, per lineage's own proven dialect kind ---
                claude_turns = [
                    TranscriptTurn.of_stored(TurnSegmentView.model_validate(t).model_dump(mode="json"))
                    for t in _segment_turns(runner_client, claude_id, claude_segments)
                ]
                opencode_turns = [
                    TranscriptTurn.of_stored(TurnSegmentView.model_validate(t).model_dump(mode="json"))
                    for t in _segment_turns(runner_client, opencode_id, opencode_segments)
                ]

                claude_events = extract_events(claude_turns, normalizer_version="claude-code-jsonl/2")
                opencode_events = extract_events(opencode_turns, normalizer_version="opencode-export/1")

                skill_events = [e for e in claude_events if e.kind == KIND_SKILL_INVOCATION]
                assert skill_events and skill_events[0].subject == "wf-commit", claude_events

                spawn_events = [e for e in opencode_events if e.kind == KIND_AGENT_SPAWN]
                assert spawn_events and spawn_events[0].subject == "reviewer", opencode_events

                # Cross-dialect: neither lineage's own turns derive the OTHER dialect's kind —
                # the tool names ("Skill" vs "task") never collide across the two dialects.
                cross_on_claude_turns = extract_events(claude_turns, normalizer_version="opencode-export/1")
                assert not [e for e in cross_on_claude_turns if e.kind == KIND_AGENT_SPAWN]
                cross_on_opencode_turns = extract_events(opencode_turns, normalizer_version="claude-code-jsonl/2")
                assert not [e for e in cross_on_opencode_turns if e.kind == KIND_SKILL_INVOCATION]
            finally:
                runner_client.close()
