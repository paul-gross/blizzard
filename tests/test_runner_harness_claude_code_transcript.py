"""``harness/internal/claude_code_transcript.py`` — the ``IHarnessTranscriptSource``
filesystem adapter (blizzard#245, phase 3).

All unit tier, hermetic under ``tmp_path`` as ``projects_root``
(``bzh:dependency-injection`` — no ``HOME`` monkey-patching), mirroring
``tests/test_runner_transcripts.py``'s repository-adapter coverage plus what is new
here: forward incremental reads from a minted position, the shared batch-budget cap,
and sidecar-backed sidechain nesting (link route 1).
"""

from __future__ import annotations

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


# --------------------------------------------------------------------------- #
# turns_since — locating the file (mirrors JsonlTranscriptRepository's coverage)
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_turns_since_hit_normalizes_the_matched_session_file(tmp_path: Path) -> None:
    _write_main(tmp_path, [fx.user_env("hello")])
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)

    assert batch.available is True
    assert batch.reason is None
    assert batch.session_id == "sess-1"
    assert batch.turns[0].text == "hello"
    assert batch.normalizer_version == NORMALIZER_VERSION
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


# --------------------------------------------------------------------------- #
# Cold (`since=None`) tail cap
# --------------------------------------------------------------------------- #


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
def test_turns_since_cold_read_of_a_small_file_is_not_truncated(tmp_path: Path) -> None:
    _write_main(tmp_path, [fx.user_env("hello")])
    source = ClaudeCodeTranscriptSource(str(tmp_path), _error_factory())

    batch = source.turns_since("sess-1", spawn_cwd="/home/user/workspace", since=None)

    assert batch.truncated is False
    assert batch.complete is True


# --------------------------------------------------------------------------- #
# Forward reads from a minted position
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Sidecar-backed sidechain nesting (link route 1)
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# read_raw_lines / size_bytes — the pre-existing operations, relocated
# --------------------------------------------------------------------------- #


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
