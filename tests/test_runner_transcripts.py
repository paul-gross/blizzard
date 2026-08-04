"""``transcripts/internal/projected_transcript_repository.py`` — the panel's read
model, end to end through the full stack.

All unit tier, hermetic under ``tmp_path`` (``bzh:dependency-injection`` — no
``HOME`` monkey-patching): fixture lines are written to a real file, read through a
real :class:`ClaudeCodeTranscriptSource`, normalized, and projected. The golden claim
is that a given set of fixture lines produces a given ``Turn`` list, so a projection
regression reads as a diff in expectations here, not silence.

File-location mechanics (session-id glob, multi-match disambiguation, the tail cap,
forward reads, sidecar discovery) are pinned once, at their own layer, in
``tests/test_runner_harness_claude_code_transcript.py`` — not duplicated here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import structlog

from blizzard.runner.harness.internal import claude_code_normalizer as normalizer_module
from blizzard.runner.harness.internal.claude_code_transcript import ClaudeCodeTranscriptSource
from blizzard.runner.harness.transcript import TranscriptErrorFactory
from blizzard.runner.transcripts.internal import projected_transcript_repository as projection_module
from blizzard.runner.transcripts.internal.projected_transcript_repository import ProjectedTranscriptRepository
from blizzard.runner.transcripts.repository import Transcript
from tests import transcript_fixtures as fx


def _repository(tmp_path: Path) -> ProjectedTranscriptRepository:
    source = ClaudeCodeTranscriptSource(str(tmp_path), TranscriptErrorFactory(structlog.get_logger("test")))
    return ProjectedTranscriptRepository(source)


def _write(tmp_path: Path, lines: list[str], *, project_dir: str = "-home-user-workspace") -> None:
    directory = tmp_path / project_dir
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sess-1.jsonl").write_text("\n".join(lines) + "\n")


def _read(tmp_path: Path) -> Transcript:
    return _repository(tmp_path).read_turns("sess-1", spawn_cwd="/home/user/workspace")


# --------------------------------------------------------------------------- #
# The turn shape
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_collapses_env_asst_and_tool_with_matched_output(tmp_path: Path) -> None:
    _write(
        tmp_path,
        [
            fx.user_env("build the thing"),
            fx.assistant_text("Sure, I'll start."),
            fx.assistant_tool_use("t1", "Bash", {"command": "ls"}),
            fx.tool_result("t1", "file1\nfile2"),
        ],
    )
    transcript = _read(tmp_path)

    assert transcript.available is True
    assert [t.kind for t in transcript.turns] == ["env", "asst", "tool"]
    assert transcript.turns[0].text == "build the thing"
    assert transcript.turns[1].text == "Sure, I'll start."
    assert transcript.turns[2].tool_name == "Bash"
    assert transcript.turns[2].tool_input == json.dumps({"command": "ls"})
    assert transcript.turns[2].tool_output == "file1\nfile2"
    assert transcript.truncated is False
    assert [t.index for t in transcript.turns] == [0, 1, 2]


@pytest.mark.unit
def test_one_assistant_record_yields_one_asst_and_n_tool_turns(tmp_path: Path) -> None:
    content = [
        {"type": "text", "text": "Checking two things."},
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "a.py"}},
        {"type": "tool_use", "id": "t2", "name": "Read", "input": {"path": "b.py"}},
    ]
    line = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": content}, "uuid": "a1"})
    _write(tmp_path, [line])
    transcript = _read(tmp_path)

    assert [t.kind for t in transcript.turns] == ["asst", "tool", "tool"]
    assert transcript.turns[1].tool_name == "Read"
    assert transcript.turns[2].tool_name == "Read"


@pytest.mark.unit
def test_unmatched_tool_result_is_dropped(tmp_path: Path) -> None:
    _write(tmp_path, [fx.tool_result("no-such-id", "orphaned output")])
    assert _read(tmp_path).turns == []


@pytest.mark.unit
def test_tool_turn_with_no_result_keeps_output_none(tmp_path: Path) -> None:
    _write(tmp_path, [fx.assistant_tool_use("t1", "Bash", {"command": "sleep 100"})])
    transcript = _read(tmp_path)

    assert len(transcript.turns) == 1
    assert transcript.turns[0].kind == "tool"
    assert transcript.turns[0].tool_output is None  # renders "running…" — the live steady state


@pytest.mark.unit
def test_is_meta_record_is_filtered(tmp_path: Path) -> None:
    _write(tmp_path, [fx.meta_record()])
    assert _read(tmp_path).turns == []


@pytest.mark.unit
def test_is_sidechain_record_is_filtered(tmp_path: Path) -> None:
    """The panel's zero-turn outcome for an ``isSidechain`` record, pinned one layer
    down: the normalizer now *surfaces* it as an unlinked conversation
    (``tests/test_runner_harness_claude_code_normalizer.py``'s own rewrite of this
    same assertion), and this projection is what re-establishes zero panel turns —
    the projection half of that two-part rewrite."""
    _write(tmp_path, [fx.sidechain_record()])
    assert _read(tmp_path).turns == []


@pytest.mark.unit
def test_a_sidecar_backed_sidechain_produces_zero_extra_panel_turns(tmp_path: Path) -> None:
    """A *resolved* sidechain (link route 1, via a real sidecar file) still
    contributes nothing beyond its own spawning tool turn — the projection drops the
    nested conversation exactly like the unresolved case above."""
    _write(
        tmp_path,
        [
            fx.assistant_tool_use("t1", "Task", {"subagent_type": "explorer", "prompt": "find X"}),
            fx.tool_result("t1", "subagent finished", agent_id="agent-abc"),
        ],
    )
    subagents_dir = tmp_path / "-home-user-workspace" / "sess-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    sidecar_lines = [fx.sidecar_record("starting", role="user", agent_id="agent-abc")]
    (subagents_dir / "agent-agent-abc.jsonl").write_text("\n".join(sidecar_lines) + "\n")

    transcript = _read(tmp_path)

    assert [t.kind for t in transcript.turns] == ["tool"]  # the spawning call, nothing nested


@pytest.mark.unit
@pytest.mark.parametrize(
    "record_type",
    [
        "mode",
        "permission-mode",
        "last-prompt",
        "ai-title",
        "queue-operation",
        "attachment",
        "file-history-snapshot",
        "file-history-delta",
        "pr-link",
    ],
)
def test_control_records_are_filtered(tmp_path: Path, record_type: str) -> None:
    _write(tmp_path, [fx.control_record(record_type)])
    assert _read(tmp_path).turns == []


@pytest.mark.unit
def test_system_record_is_filtered(tmp_path: Path) -> None:
    _write(tmp_path, [fx.control_record("system")])
    assert _read(tmp_path).turns == []


@pytest.mark.unit
def test_ansi_escapes_are_stripped_from_text(tmp_path: Path) -> None:
    _write(tmp_path, [fx.ansi_text("hello")])
    transcript = _read(tmp_path)
    assert transcript.turns[0].text == "hello"
    assert "\x1b" not in transcript.turns[0].text


@pytest.mark.unit
def test_private_mode_ansi_escapes_are_stripped_from_text(tmp_path: Path) -> None:
    _write(tmp_path, [fx.ansi_private_mode_text("hello")])
    transcript = _read(tmp_path)
    assert transcript.turns[0].text == "hello"
    assert "\x1b" not in transcript.turns[0].text


@pytest.mark.unit
def test_truncated_final_line_is_dropped_silently(tmp_path: Path) -> None:
    _write(tmp_path, [fx.user_env("build the thing"), fx.truncated_line()])
    transcript = _read(tmp_path)  # must not raise
    assert len(transcript.turns) == 1
    assert transcript.turns[0].text == "build the thing"


# --------------------------------------------------------------------------- #
# Caps — MAX_TURNS moved here; MAX_BLOCK_CHARS (text) stays in the normalizer;
# MAX_BLOCK_CHARS (tool input) is this module's own, re-materialization-time cap.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_max_turns_cap_keeps_the_most_recent_and_flags_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projection_module, "MAX_TURNS", 5)
    _write(tmp_path, [fx.user_env(f"msg-{i}", uuid=f"u{i}") for i in range(8)])
    transcript = _read(tmp_path)

    assert transcript.truncated is True
    assert len(transcript.turns) == 5
    assert [t.text for t in transcript.turns] == [f"msg-{i}" for i in range(3, 8)]
    assert [t.index for t in transcript.turns] == [0, 1, 2, 3, 4]  # re-indexed from 0


@pytest.mark.unit
def test_max_block_chars_caps_assistant_text_without_flagging_file_level_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(normalizer_module, "MAX_BLOCK_CHARS", 10)
    _write(tmp_path, [fx.assistant_text("x" * 50)])
    transcript = _read(tmp_path)

    assert len(transcript.turns[0].text) == 10
    assert transcript.turns[0].truncated is True  # block-level
    assert transcript.truncated is False  # MAX_TURNS was never hit


@pytest.mark.unit
def test_max_block_chars_caps_a_serialized_tool_input_and_flags_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Load-bearing: today's ``MAX_BLOCK_CHARS`` case exercises assistant *text* only, so
    nothing catches a re-materialized uncapped tool-input string — this is that missing
    case, at the layer that now owns the cap.
    """
    monkeypatch.setattr(projection_module, "MAX_BLOCK_CHARS", 10)
    _write(tmp_path, [fx.assistant_tool_use("t1", "Bash", {"command": "x" * 50})])
    transcript = _read(tmp_path)

    assert transcript.turns[0].kind == "tool"
    assert transcript.turns[0].tool_input is not None
    assert len(transcript.turns[0].tool_input) == 10
    assert transcript.turns[0].truncated is True


@pytest.mark.unit
def test_absent_tool_input_serializes_to_empty_string_not_json_null(tmp_path: Path) -> None:
    """The wire contract renders a missing/``null`` ``input`` as ``""`` — never
    ``json.dumps({})`` (``"{}"``), which is what re-materializing off the normalizer's
    own ``input={}`` fallback alone (with no discriminator) would produce."""
    content = [{"type": "tool_use", "id": "t1", "name": "Bash"}]  # no `input` key at all
    line = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": content}, "uuid": "a1"})
    _write(tmp_path, [line])
    transcript = _read(tmp_path)

    assert transcript.turns[0].kind == "tool"
    assert transcript.turns[0].tool_input == ""


@pytest.mark.unit
def test_bare_string_tool_input_that_parses_as_json_is_still_requoted(tmp_path: Path) -> None:
    """A bare string ``input`` (malformed relative to the tool_use schema, but seen in
    the wild) is re-quoted on the way back out, matching the wire contract's blanket
    ``json.dumps(raw_input)`` — even when the string itself happens to parse as JSON
    (``"123"``), which a re-parse-to-tell-apart heuristic gets wrong."""
    content = [{"type": "tool_use", "id": "t1", "name": "Weird", "input": "123"}]
    line = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": content}, "uuid": "a1"})
    _write(tmp_path, [line])
    transcript = _read(tmp_path)

    assert transcript.turns[0].kind == "tool"
    assert transcript.turns[0].tool_input == json.dumps("123")
