"""``transcripts/internal/projected_transcript_repository.py`` — the panel's read model.

A domain slice wired with real internal collaborators, hermetic via
``bzh:dependency-injection``. The golden claim: a given set of fixture lines produces a
given ``Turn`` list, so a projection regression reads as a diff in expectations here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import structlog

from blizzard.runner.harness.internal import claude_code_normalizer as normalizer_module
from blizzard.runner.harness.internal.claude_code_transcript import ClaudeCodeTranscriptSource
from blizzard.runner.harness.transcript import TranscriptBatch, TranscriptErrorFactory
from blizzard.runner.transcripts.internal import projected_transcript_repository as projection_module
from blizzard.runner.transcripts.internal.projected_transcript_repository import ProjectedTranscriptRepository
from blizzard.runner.transcripts.repository import Transcript
from tests import transcript_fixtures as fx
from tests.runner_fakes import FakeTranscriptSource


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


@pytest.mark.component
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
    tool = transcript.turns[2].tool
    assert tool is not None
    assert tool.name == "Bash"
    assert tool.input == {"command": "ls"}
    assert tool.output == "file1\nfile2"
    assert transcript.truncated is False
    assert [t.index for t in transcript.turns] == [0, 1, 2]


@pytest.mark.component
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
    assert transcript.turns[1].tool is not None and transcript.turns[1].tool.name == "Read"
    assert transcript.turns[2].tool is not None and transcript.turns[2].tool.name == "Read"


@pytest.mark.component
def test_unmatched_tool_result_is_dropped(tmp_path: Path) -> None:
    _write(tmp_path, [fx.tool_result("no-such-id", "orphaned output")])
    assert _read(tmp_path).turns == []


@pytest.mark.component
def test_tool_turn_with_no_result_keeps_output_none(tmp_path: Path) -> None:
    _write(tmp_path, [fx.assistant_tool_use("t1", "Bash", {"command": "sleep 100"})])
    transcript = _read(tmp_path)

    assert len(transcript.turns) == 1
    assert transcript.turns[0].kind == "tool"
    assert transcript.turns[0].tool is not None
    assert transcript.turns[0].tool.output is None  # renders "running…" — the live steady state


@pytest.mark.component
def test_is_meta_record_is_filtered(tmp_path: Path) -> None:
    _write(tmp_path, [fx.meta_record()])
    assert _read(tmp_path).turns == []


@pytest.mark.component
def test_is_sidechain_record_is_filtered(tmp_path: Path) -> None:
    """An unresolvable ``isSidechain`` record surfaces as its own top-level ``"sidechain"``
    turn (blizzard#248 D2/D7) — the unlinked routing itself is pinned in
    ``test_runner_harness_claude_code_normalizer.py``."""
    _write(tmp_path, [fx.sidechain_record()])
    transcript = _read(tmp_path)

    assert [t.kind for t in transcript.turns] == ["sidechain"]
    assert transcript.turns[0].sidechain is not None
    assert transcript.turns[0].sidechain.link == "unlinked"
    assert [t.text for t in transcript.turns[0].sidechain.turns] == ["subagent chatter"]


@pytest.mark.component
def test_a_thinking_turn_carries_through_as_its_own_kind(tmp_path: Path) -> None:
    """A thinking block is now a panel turn in its own right (blizzard#248 D2) —
    previously the projection's own ``kind != "thinking"`` filter dropped it entirely."""
    _write(tmp_path, [fx.user_env("hello"), fx.thinking_block(text="pondering", signature=None)])
    transcript = _read(tmp_path)

    assert [t.kind for t in transcript.turns] == ["env", "thinking"]
    assert transcript.turns[1].text == "pondering"
    assert transcript.turns[1].thinking_redacted is False


@pytest.mark.component
def test_a_redacted_thinking_turn_carries_presence_not_prose(tmp_path: Path) -> None:
    _write(tmp_path, [fx.thinking_block()])
    transcript = _read(tmp_path)

    assert transcript.turns[0].kind == "thinking"
    assert transcript.turns[0].text == ""
    assert transcript.turns[0].thinking_redacted is True


@pytest.mark.component
def test_a_sidecar_backed_sidechain_nests_under_its_spawning_tool_turn(tmp_path: Path) -> None:
    """A *resolved* sidechain (link route 1, via a real sidecar file) nests under its
    spawning tool turn (blizzard#248 D2/D6) — it no longer vanishes."""
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

    assert [t.kind for t in transcript.turns] == ["tool"]  # the spawning call, nothing else top-level
    sidechain = transcript.turns[0].sidechain
    assert sidechain is not None
    assert sidechain.link != "unlinked"
    assert [t.text for t in sidechain.turns] == ["starting"]
    assert sidechain.turns[0].index == 0  # indexes independently from the outer turn list


@pytest.mark.component
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


@pytest.mark.component
def test_system_record_is_filtered(tmp_path: Path) -> None:
    _write(tmp_path, [fx.control_record("system")])
    assert _read(tmp_path).turns == []


@pytest.mark.component
def test_ansi_escapes_are_stripped_from_text(tmp_path: Path) -> None:
    _write(tmp_path, [fx.ansi_text("hello")])
    transcript = _read(tmp_path)
    assert transcript.turns[0].text == "hello"
    assert "\x1b" not in transcript.turns[0].text


@pytest.mark.component
def test_private_mode_ansi_escapes_are_stripped_from_text(tmp_path: Path) -> None:
    _write(tmp_path, [fx.ansi_private_mode_text("hello")])
    transcript = _read(tmp_path)
    assert transcript.turns[0].text == "hello"
    assert "\x1b" not in transcript.turns[0].text


@pytest.mark.component
def test_truncated_final_line_is_dropped_silently(tmp_path: Path) -> None:
    _write(tmp_path, [fx.user_env("build the thing"), fx.truncated_line()])
    transcript = _read(tmp_path)  # must not raise
    assert len(transcript.turns) == 1
    assert transcript.turns[0].text == "build the thing"


# Caps — MAX_TURNS moved here; MAX_BLOCK_CHARS (text) stays in the normalizer; the tool-input
# MAX_BLOCK_CHARS below applies only once a serialized input would exceed it (blizzard#248 D2).


@pytest.mark.component
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


@pytest.mark.component
def test_max_block_chars_caps_assistant_text_without_flagging_file_level_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(normalizer_module, "MAX_BLOCK_CHARS", 10)
    _write(tmp_path, [fx.assistant_text("x" * 50)])
    transcript = _read(tmp_path)

    assert len(transcript.turns[0].text) == 10
    assert transcript.turns[0].truncated is True  # block-level
    assert transcript.truncated is False  # MAX_TURNS was never hit


@pytest.mark.component
def test_max_block_chars_degrades_an_oversized_tool_input_to_a_capped_raw_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Load-bearing: the assistant-text ``MAX_BLOCK_CHARS`` case doesn't cover an
    oversized structured tool input — this is that missing case. Below the cap, input
    stays structured (the next test); only over it does this degrade to a raw string."""
    monkeypatch.setattr(projection_module, "MAX_BLOCK_CHARS", 10)
    _write(tmp_path, [fx.assistant_tool_use("t1", "Bash", {"command": "x" * 50})])
    transcript = _read(tmp_path)

    tool = transcript.turns[0].tool
    assert transcript.turns[0].kind == "tool"
    assert tool is not None
    assert tool.input_shape == "other"
    assert tool.input == {}
    assert tool.input_unparsed is not None
    assert len(tool.input_unparsed) == 10
    assert transcript.turns[0].truncated is True


@pytest.mark.component
def test_a_tool_inputs_structure_carries_through_untouched_below_the_cap(tmp_path: Path) -> None:
    """The wire's structured ``input`` (blizzard#248 D1) — the projection no longer
    re-materializes it to a JSON string; rendering is the viewer's job."""
    _write(tmp_path, [fx.assistant_tool_use("t1", "Bash", {"command": "ls"})])
    transcript = _read(tmp_path)

    tool = transcript.turns[0].tool
    assert tool is not None
    assert tool.input_shape == "object"
    assert tool.input == {"command": "ls"}
    assert transcript.turns[0].truncated is False


# --------------------------------------------------------------------------- #
# Which of the batch's two truncation flags reaches the panel


def _scripted_batch(*, truncated: bool, sidechain_truncated: bool) -> TranscriptBatch:
    return TranscriptBatch(
        session_id="sess-1",
        available=True,
        reason=None,
        turns=[],
        unlinked_sidechains=[],
        next_position=None,
        complete=True,
        truncated=truncated,
        sidechain_truncated=sidechain_truncated,
        normalizer_version="fake/1",
        harness_version=None,
    )


@pytest.mark.unit
def test_a_sidechain_only_truncation_now_reaches_the_panels_truncated_flag() -> None:
    """Inverted by blizzard#248 D2: this projection now carries every sidechain through,
    so a sidecar-only read-budget truncation cuts content the panel renders and must
    raise its TRUNCATED banner — the opposite of when sidechains were discarded."""
    source = FakeTranscriptSource({"sess-1": _scripted_batch(truncated=False, sidechain_truncated=True)})

    transcript = ProjectedTranscriptRepository(source).read_turns("sess-1", spawn_cwd=None)

    assert transcript.truncated is True


@pytest.mark.unit
def test_a_main_file_truncation_does_reach_the_panels_truncated_flag() -> None:
    """The companion that keeps the case above from being satisfiable by ignoring both
    flags: a main-file tail cap did cut content the panel renders, so it must surface.
    """
    source = FakeTranscriptSource({"sess-1": _scripted_batch(truncated=True, sidechain_truncated=False)})

    transcript = ProjectedTranscriptRepository(source).read_turns("sess-1", spawn_cwd=None)

    assert transcript.truncated is True
