"""``harness/internal/claude_code_transcript.py`` — the transcript filesystem adapter
(blizzard#245). Unit tier, hermetic under ``tmp_path`` as ``projects_root``.

Covers forward incremental reads from a minted position, the shared batch-budget cap,
and sidecar-backed sidechain nesting (link route 1).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
import structlog
from structlog.testing import capture_logs

from blizzard.runner.harness.internal import claude_code_transcript as source_module
from blizzard.runner.harness.internal.claude_code_normalizer import NORMALIZER_VERSION
from blizzard.runner.harness.internal.claude_code_transcript import ClaudeCodeTranscriptSource, mangle_cwd
from blizzard.runner.harness.transcript import TranscriptErrorFactory
from tests import transcript_fixtures as fx


def _error_factory() -> TranscriptErrorFactory:
    return TranscriptErrorFactory(structlog.get_logger("test"))


def _write_main(tmp_path: Path, lines: list[str], *, project_dir: str = "-home-user-workspace") -> Path:
    directory = tmp_path / project_dir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "sess-1.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


# turns_since — locating the file (mirrors JsonlTranscriptRepository's coverage)


@pytest.mark.unit
def test_turns_since_hit_normalizes_the_matched_session_file(tmp_path: Path) -> None:
    _write_main(tmp_path, [fx.versioned(fx.user_env("hello"), version="2.1.220")])
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)

    assert batch.available is True
    assert batch.reason is None
    assert batch.session_id == "sess-1"
    assert batch.turns[0].text == "hello"
    assert batch.normalizer_version == NORMALIZER_VERSION
    # Asserted on the batch itself, not only on the intermediate `NormalizedFile` —
    # the stamp the issue requires is this seam's own output field.
    assert batch.harness_version == "2.1.220"
    assert batch.next_position is not None


@pytest.mark.unit
def test_turns_since_miss_is_not_found(tmp_path: Path) -> None:
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())
    batch = source.turns_since("no-such-session", spawn_cwd="/home/user/workspace", since=None)

    assert batch.available is False
    assert batch.reason == "not_found"
    assert batch.turns == []
    assert batch.unlinked_sidechains == []
    assert batch.next_position is None
    assert batch.complete is True


@pytest.mark.unit
def test_turns_since_unreadable_file_degrades_to_unreadable_reason_and_logs_once(tmp_path: Path) -> None:
    project_dir = tmp_path / "-home-user-workspace"
    project_dir.mkdir()
    # A directory named like the session file — a portable way to hit `unreadable`
    # with no dependence on permission semantics differing when tests run as root.
    (project_dir / "sess-1.jsonl").mkdir()
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    with capture_logs() as logs:
        batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)

    assert batch.available is False
    assert batch.reason == "unreadable"
    error_logs = [entry for entry in logs if entry["log_level"] == "error"]
    assert len(error_logs) == 1
    assert error_logs[0]["session_id"] == "sess-1"


@pytest.mark.unit
def test_turns_since_multi_match_prefers_the_spawn_cwd_hint(tmp_path: Path) -> None:
    _write_main(tmp_path, [fx.user_env("from wanted dir")], project_dir="-home-user-workspace")
    _write_main(tmp_path, [fx.user_env("from other dir")], project_dir="-home-user-other")
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)

    assert batch.turns[0].text == "from wanted dir"


@pytest.mark.unit
def test_turns_since_multi_match_falls_back_to_newest_mtime_without_a_hint(tmp_path: Path) -> None:
    older = _write_main(tmp_path, [fx.user_env("older")], project_dir="-home-user-older")
    newer = _write_main(tmp_path, [fx.user_env("newer")], project_dir="-home-user-newer")
    now = time.time()
    os.utime(older, (now - 100, now - 100))
    os.utime(newer, (now, now))
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    batch = source.turns_since("sess-1", spawn_cwd=None, since=None)

    assert batch.turns[0].text == "newer"


@pytest.mark.unit
def test_mangle_cwd_replaces_slashes_with_dashes() -> None:
    assert mangle_cwd("/home/user/foo") == "-home-user-foo"


# Cold (`since=None`) tail cap


@pytest.mark.unit
def test_turns_since_cold_read_tail_caps_a_pathological_file_and_flags_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = [fx.user_env(f"msg-{i}", uuid=f"u{i}") for i in range(2000)]
    line_bytes = max(len(line.encode("utf-8")) for line in lines)
    cap = line_bytes * 3
    monkeypatch.setattr(source_module, "MAX_FILE_BYTES", cap)
    path = _write_main(tmp_path, lines)
    file_size = path.stat().st_size
    assert file_size > cap * 10

    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())
    batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)

    assert batch.truncated is True
    assert batch.turns[-1].text == "msg-1999"
    assert len(batch.turns) < 10


@pytest.mark.unit
def test_a_tail_capped_sidecar_ors_its_own_truncation_into_the_sidechain_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sidecar exceeding `MAX_FILE_BYTES` reports its truncation into
    `TranscriptBatch.sidechain_truncated`, not the panel-facing `truncated` field —
    nothing the panel renders was cut, only sidechain content it already discards."""
    project_dir = "-home-user-workspace"
    main_lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "find X"}),
        fx.tool_result("t1", "spawned", agent_id="agent-abc"),
    ]
    _write_main(tmp_path, main_lines, project_dir=project_dir)

    subagents_dir = tmp_path / project_dir / "sess-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    sidecar_lines = [fx.sidecar_record(f"turn {i}", agent_id="agent-abc", uuid=f"sc{i}") for i in range(50)]
    (subagents_dir / "agent-agent-abc.jsonl").write_text("\n".join(sidecar_lines) + "\n")

    line_bytes = max(len(line.encode("utf-8")) for line in sidecar_lines)
    monkeypatch.setattr(source_module, "MAX_FILE_BYTES", line_bytes * 3)
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)

    assert batch.sidechain_truncated is True
    assert batch.truncated is False


@pytest.mark.unit
def test_turns_since_cold_read_of_a_small_file_is_not_truncated(tmp_path: Path) -> None:
    _write_main(tmp_path, [fx.user_env("hello")])
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)

    assert batch.truncated is False
    assert batch.complete is True


@pytest.mark.unit
def test_cold_read_of_a_live_appended_partial_record_mints_a_resumable_position(tmp_path: Path) -> None:
    """A cold read ending mid-record must hold the trailing fragment back from the
    minted `next_position` — minting `next_offset = size` instead would point mid-record,
    and a forward read resuming there would permanently lose the completed record."""
    project_dir = tmp_path / "-home-user-workspace"
    project_dir.mkdir(parents=True)
    path = project_dir / "sess-1.jsonl"
    complete_line = fx.user_env("first")
    straddling_line = fx.assistant_text("second")
    path.write_text(complete_line + "\n" + straddling_line[:20])  # the writer is mid-record
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    first = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)

    assert [t.text for t in first.turns] == ["first"]
    assert first.next_position is not None
    boundary = len((complete_line + "\n").encode("utf-8"))
    assert json.loads(first.next_position.token)["main"] == boundary  # a newline boundary, not EOF

    with path.open("a") as f:
        f.write(straddling_line[20:] + "\n")  # the writer completes the record

    second = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=first.next_position)

    assert [t.text for t in second.turns] == ["second"]  # re-read complete, never lost


# Forward reads from a minted position


@pytest.mark.unit
def test_turns_since_incremental_read_from_next_position_yields_only_appended_turns(tmp_path: Path) -> None:
    path = _write_main(tmp_path, [fx.user_env("first")])
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    first_batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)
    assert [t.text for t in first_batch.turns] == ["first"]
    assert first_batch.next_position is not None

    with path.open("a") as f:
        f.write(fx.assistant_text("second") + "\n")

    second_batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=first_batch.next_position)

    assert [t.text for t in second_batch.turns] == ["second"]
    assert second_batch.complete is True


@pytest.mark.unit
def test_turns_since_forward_read_with_nothing_new_yields_no_turns(tmp_path: Path) -> None:
    _write_main(tmp_path, [fx.user_env("first")])
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    first_batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)
    assert first_batch.next_position is not None
    second_batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=first_batch.next_position)

    assert second_batch.turns == []
    assert second_batch.complete is True


@pytest.mark.unit
def test_a_position_past_the_files_current_size_starts_that_file_over(tmp_path: Path) -> None:
    """A `main` offset past the file's current size (truncated/replaced since minted)
    is as malformed as a negative offset — clamped to 0 (start over), not reaching
    `_read_forward`'s own clamp, whose negative delta would inflate the budget."""
    from blizzard.runner.harness.transcript import TranscriptPosition

    path = _write_main(tmp_path, [fx.user_env("hello")])
    file_size = path.stat().st_size
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    past_eof_position = TranscriptPosition(token=f'{{"main": {file_size + 10_000}, "sidecars": {{}}}}')
    batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=past_eof_position)

    assert [t.text for t in batch.turns] == ["hello"]
    assert batch.complete is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "token",
    [
        "not json at all",  # non-JSON token
        '"a bare string"',  # JSON, but not a dict
        "[1, 2, 3]",  # JSON, but not a dict
        '{"main": "zero", "sidecars": {}}',  # wrong-typed main
        '{"main": -5, "sidecars": {}}',  # negative main
        '{"main": 0, "sidecars": [1, 2]}',  # wrong-typed sidecars
        '{"main": 0, "sidecars": {"agent-abc": -7}}',  # negative sidecar offset
        '{"main": 0, "sidecars": {"agent-abc": "x", "7": 0}}',  # wrong-typed sidecar entries
    ],
)
def test_a_malformed_position_token_starts_over_rather_than_degrading_to_unreadable(tmp_path: Path, token: str) -> None:
    """A foreign or malformed position token degrades to "start over", never a raise
    or `available=False, reason="unreadable"` — each parametrized token targets one
    tolerant-decode branch of `_decode_position`."""
    from blizzard.runner.harness.transcript import TranscriptPosition

    _write_main(tmp_path, [fx.user_env("hello")])
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=TranscriptPosition(token=token))

    assert batch.available is True
    assert batch.reason is None
    assert [t.text for t in batch.turns] == ["hello"]  # the whole file, from offset 0


@pytest.mark.unit
def test_read_forward_a_record_wider_than_the_budget_makes_forward_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A window consuming a FULL `MAX_BATCH_BYTES` with no newline proves the record
    is at least that wide, so it must force progress rather than stall forever — but
    only when `budget` is the real per-call ceiling, not an arbitrary narrow value."""
    path = tmp_path / "wide.jsonl"
    path.write_bytes(b"x" * 100)  # one line, no newline anywhere, wider than the budget below
    monkeypatch.setattr(source_module, "MAX_BATCH_BYTES", 10)

    result = source_module._read_forward(path, start_offset=0, budget=10)

    assert result.hit_budget is True
    assert result.next_offset == 10  # forced whole-window consumption, not a stall at 0


@pytest.mark.unit
def test_read_forward_a_narrow_non_ceiling_budget_never_force_consumes(tmp_path: Path) -> None:
    """F2 regression: a sidecar's `budget=remaining_budget` can be arbitrarily small,
    so a narrow window finding no newline proves nothing about the record's real
    width and must NOT force-consume — zero progress, retried on a fuller budget."""
    path = tmp_path / "sidecar.jsonl"
    path.write_bytes(b"x" * 100)  # one ordinary-width record — not oversized
    assert source_module.MAX_BATCH_BYTES > 10  # the budget below is a small fraction of the real ceiling

    result = source_module._read_forward(path, start_offset=0, budget=10)

    assert result.hit_budget is True  # more remains — correctly reported
    assert result.next_offset == 0  # but NOT force-consumed
    assert result.lines == []


@pytest.mark.unit
def test_read_forward_a_live_appended_partial_line_within_the_budget_waits(tmp_path: Path) -> None:
    """The companion case: the window reaches the file's own current end, short of
    budget, with no newline — the ordinary live-appended trailing fragment. Must make
    zero progress, not force-consume a fragment `normalize_lines` can't parse."""
    path = tmp_path / "partial.jsonl"
    path.write_bytes(b"x" * 5)  # shorter than the budget below, still no newline

    result = source_module._read_forward(path, start_offset=0, budget=100)

    assert result.hit_budget is False
    assert result.next_offset == 0
    assert result.lines == []


@pytest.mark.unit
def test_sidecar_join_preserves_an_already_attached_inline_sidechain(tmp_path: Path) -> None:
    """A sidecar (route 1) join must not silently discard an inline sidechain (route
    2/3) already resolved onto the same tool turn — the displaced conversation
    surfaces on `unlinked_sidechains`, re-stamped `link="unlinked"`, not lost."""
    main_lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "find X"}, uuid="a1"),
        fx.sidechain_run_record("inline chatter", uuid="inline-1", parent_uuid="a1"),
        fx.tool_result("t1", "subagent finished", agent_id="agent-abc"),
    ]
    project_dir = "-home-user-workspace"
    _write_main(tmp_path, main_lines, project_dir=project_dir)

    subagents_dir = tmp_path / project_dir / "sess-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    sidecar_lines = [fx.sidecar_record("sidecar chatter", agent_id="agent-abc")]
    (subagents_dir / "agent-agent-abc.jsonl").write_text("\n".join(sidecar_lines) + "\n")

    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())
    batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)

    tool_turn = next(t for t in batch.turns if t.kind == "tool")
    assert tool_turn.sidechain is not None
    assert tool_turn.sidechain.link == "agent-id"
    assert [t.text for t in tool_turn.sidechain.turns] == ["sidecar chatter"]

    assert len(batch.unlinked_sidechains) == 1
    displaced = batch.unlinked_sidechains[0]
    assert displaced.link == "unlinked"
    assert [t.text for t in displaced.turns] == ["inline chatter"]


@pytest.mark.unit
def test_turns_since_batch_budget_exhaustion_returns_incomplete_with_a_next_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = [fx.user_env(f"msg-{i}", uuid=f"u{i}") for i in range(200)]
    _write_main(tmp_path, lines)
    line_bytes = max(len(line.encode("utf-8")) for line in lines)
    monkeypatch.setattr(source_module, "MAX_BATCH_BYTES", line_bytes * 5)
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    from blizzard.runner.harness.transcript import TranscriptPosition

    start_position = TranscriptPosition(token='{"main": 0, "sidecars": {}}')
    batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=start_position)

    assert batch.complete is False
    assert 0 < len(batch.turns) < 200
    assert batch.next_position is not None

    # Looping on `next_position` until `complete=True` eventually reads everything.
    all_texts = [t.text for t in batch.turns]
    position = batch.next_position
    while not batch.complete:
        batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=position)
        all_texts.extend(t.text for t in batch.turns)
        position = batch.next_position
    assert all_texts == [f"msg-{i}" for i in range(200)]


# Sidecar-backed sidechain nesting (link route 1)


@pytest.mark.unit
def test_sidecar_backed_sidechain_nests_under_its_spawning_tool_call_by_agent_id(tmp_path: Path) -> None:
    main_lines = [
        fx.assistant_tool_use("t1", "Task", {"subagent_type": "explorer", "prompt": "find X"}),
        fx.tool_result("t1", "subagent finished", agent_id="agent-abc"),
    ]
    project_dir = "-home-user-workspace"
    _write_main(tmp_path, main_lines, project_dir=project_dir)

    subagents_dir = tmp_path / project_dir / "sess-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    sidecar_lines = [
        fx.sidecar_record("starting", role="user", agent_id="agent-abc"),
        fx.sidecar_record("found X", role="assistant", agent_id="agent-abc"),
    ]
    (subagents_dir / "agent-agent-abc.jsonl").write_text("\n".join(sidecar_lines) + "\n")

    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())
    batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)

    tool_turn = next(t for t in batch.turns if t.kind == "tool")
    sidechain = tool_turn.sidechain
    assert sidechain is not None
    assert sidechain.link == "agent-id"
    assert sidechain.agent_id == "agent-abc"
    assert sidechain.agent_type == "explorer"
    assert [t.text for t in sidechain.turns] == ["starting", "found X"]
    assert batch.unlinked_sidechains == []


@pytest.mark.unit
def test_a_sidecar_resolved_after_its_spawning_batch_lands_unlinked_not_agent_id(
    tmp_path: Path,
) -> None:
    """A sidecar whose spawning tool turn was already delivered in an earlier batch
    surfaces on `unlinked_sidechains` with `link="unlinked"`, matching every other
    producer of that list — never `"agent-id"`, regardless of which producer landed it."""
    project_dir = "-home-user-workspace"
    main_lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "find X"}),
        fx.tool_result("t1", "spawned", agent_id="agent-abc"),
    ]
    path = _write_main(tmp_path, main_lines, project_dir=project_dir)
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    # First batch: the sidecar doesn't exist on disk yet, so the agent id is carried
    # forward as a candidate with no sidechain attached this call.
    first_batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)
    assert first_batch.next_position is not None

    subagents_dir = tmp_path / project_dir / "sess-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    sidecar_lines = [fx.sidecar_record("found X", agent_id="agent-abc")]
    (subagents_dir / "agent-agent-abc.jsonl").write_text("\n".join(sidecar_lines) + "\n")
    with path.open("a") as f:
        f.write(fx.user_env("unrelated later turn") + "\n")

    # Second batch resumes past the tool call's own line — its spawning turn is not
    # part of this call's `normalized.turns` at all.
    second_batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=first_batch.next_position)

    tool_turns = [t for t in second_batch.turns if t.kind == "tool"]
    assert tool_turns == []
    assert len(second_batch.unlinked_sidechains) == 1
    assert second_batch.unlinked_sidechains[0].link == "unlinked"
    assert [t.text for t in second_batch.unlinked_sidechains[0].turns] == ["found X"]


@pytest.mark.unit
def test_a_tool_use_tool_result_pair_straddling_a_batch_boundary_still_discovers_the_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1 regression: a `tool_use`/`tool_result` pair split across a forward-read
    boundary must not permanently lose the sidecar it names — a `tool_result` landing
    alone must still surface the sidecar unlinked, not dropped."""
    project_dir = "-home-user-workspace"
    main_lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "find X"}, uuid="a1"),
        fx.tool_result("t1", "spawned", agent_id="agent-abc"),
    ]
    _write_main(tmp_path, main_lines, project_dir=project_dir)

    subagents_dir = tmp_path / project_dir / "sess-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    sidecar_lines = [fx.sidecar_record("found X", agent_id="agent-abc")]
    (subagents_dir / "agent-agent-abc.jsonl").write_text("\n".join(sidecar_lines) + "\n")

    # Force the forward read to stop after exactly the `tool_use` record's own
    # line, splitting it from its `tool_result` at the read boundary.
    first_line_bytes = len(main_lines[0].encode("utf-8")) + 1  # + the newline
    monkeypatch.setattr(source_module, "MAX_BATCH_BYTES", first_line_bytes)
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    from blizzard.runner.harness.transcript import TranscriptPosition

    start_position = TranscriptPosition(token='{"main": 0, "sidecars": {}}')
    first = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=start_position)
    assert first.complete is False
    tool_turn = next(t for t in first.turns if t.kind == "tool")
    assert tool_turn.sidechain is None  # the `tool_result` hasn't been read yet

    monkeypatch.setattr(source_module, "MAX_BATCH_BYTES", 1_000_000)
    second = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=first.next_position)

    # The `tool_result` lands alone in this second call — its spawning turn was
    # already delivered in the first — so it must still surface, unlinked not dropped.
    assert [t for t in second.turns if t.kind == "tool"] == []
    assert len(second.unlinked_sidechains) == 1
    assert second.unlinked_sidechains[0].link == "unlinked"
    assert [t.text for t in second.unlinked_sidechains[0].turns] == ["found X"]


@pytest.mark.unit
def test_missing_sidecar_leaves_the_candidate_tool_call_without_a_sidechain(tmp_path: Path) -> None:
    main_lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "find X"}),
        fx.tool_result("t1", "subagent finished", agent_id="agent-missing"),
    ]
    _write_main(tmp_path, main_lines)
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)

    tool_turn = next(t for t in batch.turns if t.kind == "tool")
    assert tool_turn.sidechain is None


@pytest.mark.unit
def test_cold_read_gates_the_sidecar_fanout_on_a_shared_budget_and_flags_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cold read's sidecar fan-out is gated by `MAX_BATCH_BYTES`, not just each
    sidecar's own `MAX_FILE_BYTES` — without the shared-budget gate, bytes read scale
    with sidecar count, unbounded. Flagged on `sidechain_truncated`, not `truncated`."""
    project_dir = "-home-user-workspace"
    n_sidecars = 6
    main_lines = []
    for i in range(n_sidecars):
        main_lines.append(fx.assistant_tool_use(f"t{i}", "Task", {"prompt": f"job {i}"}, uuid=f"a{i}"))
        main_lines.append(fx.tool_result(f"t{i}", "spawned", agent_id=f"agent-{i}"))
    _write_main(tmp_path, main_lines, project_dir=project_dir)

    subagents_dir = tmp_path / project_dir / "sess-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    sidecar_paths = []
    for i in range(n_sidecars):
        sidecar_path = subagents_dir / f"agent-agent-{i}.jsonl"
        sidecar_path.write_text(fx.sidecar_record("x" * 200, agent_id=f"agent-{i}") + "\n")
        sidecar_paths.append(sidecar_path)
    per_sidecar_bytes = max(p.stat().st_size for p in sidecar_paths)

    # Budget enough for fewer than `n_sidecars` sidecars, so the fan-out must stop
    # partway through rather than reading every discovered sidecar.
    monkeypatch.setattr(source_module, "MAX_BATCH_BYTES", per_sidecar_bytes * (n_sidecars - 2))
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    with capture_logs() as logs:
        batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)

    nested = [t for t in batch.turns if t.kind == "tool" and t.sidechain is not None]
    assert 0 < len(nested) < n_sidecars
    assert batch.sidechain_truncated is True
    assert batch.truncated is False
    warning_logs = [entry for entry in logs if entry["log_level"] == "warning"]
    assert len(warning_logs) == n_sidecars - len(nested)
    assert all("agent_id" in entry for entry in warning_logs)


@pytest.mark.unit
def test_cold_read_records_a_budget_skipped_sidecar_so_a_bootstrapped_forward_lane_finds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cold read mints a `next_position` a future forward lane can bootstrap from.
    A sidecar the shared budget never reaches must still be recorded into it (at
    offset 0), exactly as the forward path does, or it falls out of consideration."""
    project_dir = "-home-user-workspace"
    main_lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "job"}, uuid="a1"),
        fx.tool_result("t1", "spawned", agent_id="agent-skipped"),
    ]
    _write_main(tmp_path, main_lines, project_dir=project_dir)

    subagents_dir = tmp_path / project_dir / "sess-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    (subagents_dir / "agent-agent-skipped.jsonl").write_text(fx.sidecar_record("x", agent_id="agent-skipped") + "\n")

    # Zero budget: the sidecar is discovered but the shared fan-out budget never
    # admits it.
    monkeypatch.setattr(source_module, "MAX_BATCH_BYTES", 0)
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)

    assert batch.sidechain_truncated is True
    assert batch.next_position is not None
    position_data = json.loads(batch.next_position.token)
    assert position_data["sidecars"].get("agent-skipped") == 0


@pytest.mark.unit
def test_forward_read_carries_a_budget_skipped_sidecar_forward_as_a_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sidecar discovered but not yet reached by a forward read's shared budget is
    still recorded in `next_position` at offset 0 — a later call reopens it rather
    than losing it once its spawning line scrolls out of the read window."""
    project_dir = "-home-user-workspace"
    main_lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "find X"}, uuid="a1"),
        fx.tool_result("t1", "spawned", agent_id="agent-abc"),
    ]
    _write_main(tmp_path, main_lines, project_dir=project_dir)

    subagents_dir = tmp_path / project_dir / "sess-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    sidecar_lines = [fx.sidecar_record(f"turn {i}", agent_id="agent-abc", uuid=f"sc{i}") for i in range(5)]
    (subagents_dir / "agent-agent-abc.jsonl").write_text("\n".join(sidecar_lines) + "\n")

    main_path = tmp_path / project_dir / "sess-1.jsonl"
    monkeypatch.setattr(source_module, "MAX_BATCH_BYTES", main_path.stat().st_size)
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    from blizzard.runner.harness.transcript import TranscriptPosition

    start_position = TranscriptPosition(token='{"main": 0, "sidecars": {}}')
    first = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=start_position)

    # The main file consumed the whole budget this call, so the sidecar is a
    # newly-discovered, budget-skipped candidate -- never opened, but still carried.
    assert first.complete is False
    tool_turn = next(t for t in first.turns if t.kind == "tool")
    assert tool_turn.sidechain is None
    assert first.next_position is not None
    assert '"agent-abc": 0' in first.next_position.token

    monkeypatch.setattr(source_module, "MAX_BATCH_BYTES", 1_000_000)
    second = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=first.next_position)

    assert second.complete is True
    assert len(second.unlinked_sidechains) == 1
    assert [t.text for t in second.unlinked_sidechains[0].turns] == [f"turn {i}" for i in range(5)]


@pytest.mark.unit
def test_forward_read_carries_a_not_yet_flushed_sidecar_forward_as_a_candidate(tmp_path: Path) -> None:
    """A sidecar named by this batch's `tool_result` but not yet flushed to disk is
    still recorded in `next_position` at offset 0, so a later call — once the harness
    writes the file — finds it rather than losing it once the window scrolls past."""
    project_dir = "-home-user-workspace"
    main_lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "find X"}, uuid="a1"),
        fx.tool_result("t1", "spawned", agent_id="agent-abc"),
    ]
    _write_main(tmp_path, main_lines, project_dir=project_dir)
    # No `subagents/` directory at all yet -- the sidecar file doesn't exist.
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    from blizzard.runner.harness.transcript import TranscriptPosition

    start_position = TranscriptPosition(token='{"main": 0, "sidecars": {}}')
    first = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=start_position)

    tool_turn = next(t for t in first.turns if t.kind == "tool")
    assert tool_turn.sidechain is None
    assert first.next_position is not None
    assert '"agent-abc": 0' in first.next_position.token

    # The harness flushes the sidecar file after the fact; a later call now finds it.
    subagents_dir = tmp_path / project_dir / "sess-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    (subagents_dir / "agent-agent-abc.jsonl").write_text(fx.sidecar_record("late", agent_id="agent-abc") + "\n")

    second = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=first.next_position)

    assert len(second.unlinked_sidechains) == 1
    assert [t.text for t in second.unlinked_sidechains[0].turns] == ["late"]


@pytest.mark.unit
def test_forward_read_resumes_a_partially_delivered_sidecar_from_its_recorded_offset(tmp_path: Path) -> None:
    """The sidecar carry-forward at a non-zero offset: a second forward call must
    deliver only turns appended since the first call's recorded offset — restarting
    at 0 would re-deliver the whole conversation as a duplicate."""
    project_dir = "-home-user-workspace"
    main_lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "find X"}, uuid="a1"),
        fx.tool_result("t1", "spawned", agent_id="agent-abc"),
    ]
    _write_main(tmp_path, main_lines, project_dir=project_dir)

    subagents_dir = tmp_path / project_dir / "sess-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    sidecar_path = subagents_dir / "agent-agent-abc.jsonl"
    sidecar_lines = [
        fx.sidecar_record("already shipped 1", agent_id="agent-abc", uuid="sc1"),
        fx.sidecar_record("already shipped 2", agent_id="agent-abc", uuid="sc2"),
    ]
    sidecar_path.write_text("\n".join(sidecar_lines) + "\n")
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    from blizzard.runner.harness.transcript import TranscriptPosition

    start_position = TranscriptPosition(token='{"main": 0, "sidecars": {}}')
    first = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=start_position)
    assert first.complete is True
    tool_turn = next(t for t in first.turns if t.kind == "tool")
    assert tool_turn.sidechain is not None
    assert [t.text for t in tool_turn.sidechain.turns] == ["already shipped 1", "already shipped 2"]
    assert first.next_position is not None
    recorded_offset = json.loads(first.next_position.token)["sidecars"]["agent-abc"]
    assert recorded_offset == sidecar_path.stat().st_size  # a real non-zero offset carried forward

    with sidecar_path.open("a") as f:
        f.write(fx.sidecar_record("appended later", agent_id="agent-abc", uuid="sc3") + "\n")

    second = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=first.next_position)

    # Only the appended turn — the two already-shipped ones are never re-delivered.
    assert len(second.unlinked_sidechains) == 1
    assert [t.text for t in second.unlinked_sidechains[0].turns] == ["appended later"]


@pytest.mark.unit
def test_a_sidecar_position_past_its_files_current_size_starts_that_sidecar_over(tmp_path: Path) -> None:
    """The sidecar twin of the main file's past-EOF clamp: a stale recorded offset
    restarts that sidecar from 0 rather than reaching `_read_forward`'s own clamp
    (whose negative delta would inflate the budget) or silently skipping its content."""
    project_dir = "-home-user-workspace"
    main_lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "find X"}, uuid="a1"),
        fx.tool_result("t1", "spawned", agent_id="agent-abc"),
    ]
    main_path = _write_main(tmp_path, main_lines, project_dir=project_dir)

    subagents_dir = tmp_path / project_dir / "sess-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    sidecar_path = subagents_dir / "agent-agent-abc.jsonl"
    sidecar_path.write_text(fx.sidecar_record("survives the restart", agent_id="agent-abc") + "\n")
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    from blizzard.runner.harness.transcript import TranscriptPosition

    stale = TranscriptPosition(token=f'{{"main": {main_path.stat().st_size}, "sidecars": {{"agent-abc": 999999}}}}')
    batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=stale)

    assert len(batch.unlinked_sidechains) == 1
    assert [t.text for t in batch.unlinked_sidechains[0].turns] == ["survives the restart"]
    assert batch.next_position is not None
    assert json.loads(batch.next_position.token)["sidecars"]["agent-abc"] == sidecar_path.stat().st_size


@pytest.mark.unit
def test_a_fully_caught_up_sidecar_does_not_force_an_incomplete_batch_when_the_budget_is_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A forward read whose shared budget is spent by the main file must not report
    `complete=False` for a sidecar with nothing unread — its recorded offset already
    equals its file size, so a looping consumer must not make a no-op round trip."""
    project_dir = "-home-user-workspace"
    main_lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "find X"}, uuid="a1"),
        fx.tool_result("t1", "spawned", agent_id="agent-abc"),
    ]
    main_path = _write_main(tmp_path, main_lines, project_dir=project_dir)

    subagents_dir = tmp_path / project_dir / "sess-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    sidecar_path = subagents_dir / "agent-agent-abc.jsonl"
    sidecar_path.write_text(fx.sidecar_record("all read", agent_id="agent-abc") + "\n")
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    from blizzard.runner.harness.transcript import TranscriptPosition

    start_position = TranscriptPosition(token='{"main": 0, "sidecars": {}}')
    first = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=start_position)
    assert first.complete is True  # everything, sidecar included, was delivered

    appended = fx.user_env("appended to main only", uuid="u9") + "\n"
    with main_path.open("a") as f:
        f.write(appended)
    # The appended bytes consume the whole budget, so the sidecar candidate is
    # reached with `remaining_budget == 0` — but it has no unread bytes.
    monkeypatch.setattr(source_module, "MAX_BATCH_BYTES", len(appended.encode("utf-8")))

    second = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=first.next_position)

    assert [t.text for t in second.turns] == ["appended to main only"]
    assert second.complete is True  # nothing unread anywhere — no no-op round trip owed


@pytest.mark.unit
def test_a_sidecar_record_wider_than_the_leftover_budget_survives_to_a_later_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F2, observed at `turns_since`: a sidecar reads with whatever budget survives
    the main file's read, which can be smaller than one record. That narrow window
    must make zero progress and the record must arrive intact later, not be dropped."""
    project_dir = "-home-user-workspace"
    main_lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "find X"}, uuid="a1"),
        fx.tool_result("t1", "spawned", agent_id="agent-abc"),
    ]
    main_path = _write_main(tmp_path, main_lines, project_dir=project_dir)

    subagents_dir = tmp_path / project_dir / "sess-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    sidecar_path = subagents_dir / "agent-agent-abc.jsonl"
    sidecar_record = fx.sidecar_record("must not be dropped", agent_id="agent-abc")
    sidecar_path.write_text(sidecar_record + "\n")

    # Budget = the whole main file plus a leftover smaller than one sidecar record.
    main_size = main_path.stat().st_size
    leftover = len(sidecar_record.encode("utf-8")) // 2
    monkeypatch.setattr(source_module, "MAX_BATCH_BYTES", main_size + leftover)
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    from blizzard.runner.harness.transcript import TranscriptPosition

    start_position = TranscriptPosition(token='{"main": 0, "sidecars": {}}')
    first = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=start_position)

    assert first.complete is False  # the sidecar still has unread content
    assert first.next_position is not None
    assert json.loads(first.next_position.token)["sidecars"]["agent-abc"] == 0  # zero progress, not a drop

    monkeypatch.setattr(source_module, "MAX_BATCH_BYTES", 1_000_000)
    second = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=first.next_position)

    assert len(second.unlinked_sidechains) == 1
    assert [t.text for t in second.unlinked_sidechains[0].turns] == ["must not be dropped"]


@pytest.mark.unit
def test_a_sidecar_that_fails_to_open_logs_warning_and_the_batch_stays_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sidecar `OSError` is recovered, not a boundary failure: `from_io_recovered`
    (WARNING), batch still `available=True` — distinct from the main file's own open
    failure, a boundary failure logged at ERROR via `from_io` that aborts the read."""
    project_dir = "-home-user-workspace"
    main_lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "find X"}),
        fx.tool_result("t1", "spawned", agent_id="agent-abc"),
    ]
    _write_main(tmp_path, main_lines, project_dir=project_dir)

    subagents_dir = tmp_path / project_dir / "sess-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    sidecar_path = subagents_dir / "agent-agent-abc.jsonl"
    sidecar_path.write_text(fx.sidecar_record("hello", agent_id="agent-abc") + "\n")

    # `is_file()` gates entry, so a directory never reaches `open()` for a sidecar —
    # patch `_read_cold` to raise for this path instead, portable across root/non-root.
    orig_read_cold = source_module._read_cold

    def failing_read_cold(path: Path) -> object:
        if path == sidecar_path:
            raise OSError("simulated sidecar read failure")
        return orig_read_cold(path)

    monkeypatch.setattr(source_module, "_read_cold", failing_read_cold)
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    with capture_logs() as logs:
        batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)

    assert batch.available is True
    tool_turn = next(t for t in batch.turns if t.kind == "tool")
    assert tool_turn.sidechain is None
    warning_logs = [entry for entry in logs if entry["log_level"] == "warning"]
    assert len(warning_logs) == 1
    error_logs = [entry for entry in logs if entry["log_level"] == "error"]
    assert error_logs == []


# read_raw_lines / size_bytes — the pre-existing operations, relocated


@pytest.mark.unit
def test_read_raw_lines_hit_returns_the_files_lines(tmp_path: Path) -> None:
    _write_main(tmp_path, [fx.user_env("hello")])
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    lines = source.read_raw_lines("sess-1", spawn_cwd="/home/user/workspace")
    assert len(lines) == 1


@pytest.mark.unit
def test_read_raw_lines_miss_is_empty(tmp_path: Path) -> None:
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())
    assert source.read_raw_lines("no-such-session", spawn_cwd=None) == []


@pytest.mark.unit
def test_size_bytes_reports_the_located_transcripts_size(tmp_path: Path) -> None:
    path = _write_main(tmp_path, [fx.user_env("hello")])
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    assert source.size_bytes("sess-1", spawn_cwd="/home/user/workspace") == path.stat().st_size


@pytest.mark.unit
def test_size_bytes_of_a_missing_transcript_is_unknown_not_zero(tmp_path: Path) -> None:
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())
    assert source.size_bytes("no-such-session", spawn_cwd="/home/user/workspace") is None


@pytest.mark.unit
def test_size_bytes_measures_a_transcript_far_larger_than_the_read_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_module, "MAX_FILE_BYTES", 200)
    project_dir = tmp_path / "-home-user-workspace"
    project_dir.mkdir()
    (project_dir / "sess-big.jsonl").write_text("x" * 1200)
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    assert source.size_bytes("sess-big", spawn_cwd="/home/user/workspace") == 1200


@pytest.mark.unit
def test_read_raw_lines_unreadable_logs_warning_not_error(tmp_path: Path) -> None:
    """Both callers (the envelope-less usage fallback, the rotation size check)
    continue past an empty/`None` reply rather than aborting — a recoverable
    condition, WARNING per `bzh:structlog-logging`, not the boundary-failure ERROR."""
    project_dir = tmp_path / "-home-user-workspace"
    project_dir.mkdir()
    (project_dir / "sess-1.jsonl").mkdir()  # a directory forces OSError on open
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    with capture_logs() as logs:
        lines = source.read_raw_lines("sess-1", spawn_cwd="/home/user/workspace")

    assert lines == []
    assert [entry["log_level"] for entry in logs] == ["warning"]


@pytest.mark.unit
def test_size_bytes_unreadable_logs_warning_not_error(tmp_path: Path) -> None:
    project_dir = tmp_path / "-home-user-workspace"
    project_dir.mkdir()
    # `stat()` succeeds on a plain directory, so a broken symlink is the portable way
    # to force `OSError`: `stat()` follows it and raises `FileNotFoundError`.
    (project_dir / "sess-1.jsonl").symlink_to(project_dir / "does-not-exist")
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    with capture_logs() as logs:
        size = source.size_bytes("sess-1", spawn_cwd="/home/user/workspace")

    assert size is None
    assert [entry["log_level"] for entry in logs] == ["warning"]
