"""``transcripts/`` — locator, parser, and the filesystem repository (issue #29).

All unit tier (`blizzard-harness:/verification/blizzard.md`): the parser takes an
iterable of strings and needs no filesystem; the repository is exercised with
``tmp_path`` as ``projects_root`` (``bzh:dependency-injection`` is what makes this
hermetic — no ``HOME`` monkey-patching).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
import structlog

from blizzard.runner.transcripts import parser as parser_module
from blizzard.runner.transcripts.internal.jsonl_transcript_repository import (
    JsonlTranscriptRepository,
)
from blizzard.runner.transcripts.locator import mangle_cwd
from blizzard.runner.transcripts.parser import parse_turns
from blizzard.runner.transcripts.repository import TranscriptErrorFactory
from tests import transcript_fixtures as fx

# --------------------------------------------------------------------------- #
# locator.mangle_cwd — pure, no I/O
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_mangle_cwd_replaces_slashes_with_dashes() -> None:
    assert mangle_cwd("/home/user/foo") == "-home-user-foo"


# --------------------------------------------------------------------------- #
# parser.parse_turns — the record → turn collapse
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_collapses_env_asst_and_tool_with_matched_output() -> None:
    lines = [
        fx.user_env("build the thing"),
        fx.assistant_text("Sure, I'll start."),
        fx.assistant_tool_use("t1", "Bash", {"command": "ls"}),
        fx.tool_result("t1", "file1\nfile2"),
    ]
    parsed = parse_turns(lines)

    assert [t.kind for t in parsed.turns] == ["env", "asst", "tool"]
    assert parsed.turns[0].text == "build the thing"
    assert parsed.turns[1].text == "Sure, I'll start."
    assert parsed.turns[2].tool_name == "Bash"
    assert parsed.turns[2].tool_input == json.dumps({"command": "ls"})
    assert parsed.turns[2].tool_output == "file1\nfile2"
    assert parsed.truncated is False
    assert [t.index for t in parsed.turns] == [0, 1, 2]


@pytest.mark.unit
def test_one_assistant_record_yields_one_asst_and_n_tool_turns() -> None:
    content = [
        {"type": "text", "text": "Checking two things."},
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "a.py"}},
        {"type": "tool_use", "id": "t2", "name": "Read", "input": {"path": "b.py"}},
    ]
    line = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": content}, "timestamp": None})
    parsed = parse_turns([line])

    assert [t.kind for t in parsed.turns] == ["asst", "tool", "tool"]
    assert parsed.turns[1].tool_name == "Read"
    assert parsed.turns[2].tool_name == "Read"


@pytest.mark.unit
def test_unmatched_tool_result_is_dropped() -> None:
    lines = [fx.tool_result("no-such-id", "orphaned output")]
    parsed = parse_turns(lines)
    assert parsed.turns == []


@pytest.mark.unit
def test_tool_turn_with_no_result_keeps_output_none() -> None:
    lines = [fx.assistant_tool_use("t1", "Bash", {"command": "sleep 100"})]
    parsed = parse_turns(lines)
    assert len(parsed.turns) == 1
    assert parsed.turns[0].kind == "tool"
    assert parsed.turns[0].tool_output is None  # renders "running…" — the live steady state


@pytest.mark.unit
def test_is_meta_record_is_filtered() -> None:
    parsed = parse_turns([fx.meta_record()])
    assert parsed.turns == []


@pytest.mark.unit
def test_is_sidechain_record_is_filtered() -> None:
    parsed = parse_turns([fx.sidechain_record()])
    assert parsed.turns == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "record_type", ["mode", "permission-mode", "last-prompt", "ai-title", "queue-operation", "attachment"]
)
def test_control_records_are_filtered(record_type: str) -> None:
    parsed = parse_turns([fx.control_record(record_type)])
    assert parsed.turns == []


@pytest.mark.unit
def test_system_record_is_filtered() -> None:
    parsed = parse_turns([fx.control_record("system")])
    assert parsed.turns == []


@pytest.mark.unit
def test_ansi_escapes_are_stripped_from_text() -> None:
    parsed = parse_turns([fx.ansi_text("hello")])
    assert parsed.turns[0].text == "hello"
    assert "\x1b" not in parsed.turns[0].text


@pytest.mark.unit
def test_private_mode_ansi_escapes_are_stripped_from_text() -> None:
    """Private-mode CSI (`\\x1b[?25l`) strips too — a real-transcript finding.

    Pinned separately from :func:`test_ansi_escapes_are_stripped_from_text` because the
    `?` private-mode parameter prefix is a distinct part of the CSI grammar from the
    SGR subset that fixture covers.
    """
    parsed = parse_turns([fx.ansi_private_mode_text("hello")])
    assert parsed.turns[0].text == "hello"
    assert "\x1b" not in parsed.turns[0].text


@pytest.mark.unit
def test_truncated_final_line_is_dropped_silently() -> None:
    lines = [fx.user_env("build the thing"), fx.truncated_line()]
    parsed = parse_turns(lines)  # must not raise
    assert len(parsed.turns) == 1
    assert parsed.turns[0].text == "build the thing"


@pytest.mark.unit
def test_max_turns_cap_keeps_the_most_recent_and_flags_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parser_module, "MAX_TURNS", 5)
    lines = [fx.user_env(f"msg-{i}", uuid=f"u{i}") for i in range(8)]
    parsed = parse_turns(lines)

    assert parsed.truncated is True
    assert len(parsed.turns) == 5
    assert [t.text for t in parsed.turns] == [f"msg-{i}" for i in range(3, 8)]
    assert [t.index for t in parsed.turns] == [0, 1, 2, 3, 4]  # re-indexed from 0


@pytest.mark.unit
def test_max_block_chars_caps_a_turn_without_flagging_file_level_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parser_module, "MAX_BLOCK_CHARS", 10)
    parsed = parse_turns([fx.assistant_text("x" * 50)])

    assert len(parsed.turns[0].text) == 10
    assert parsed.turns[0].truncated is True  # block-level
    assert parsed.truncated is False  # MAX_TURNS cap was never hit


# --------------------------------------------------------------------------- #
# JsonlTranscriptRepository — the filesystem adapter
# --------------------------------------------------------------------------- #


def _error_factory() -> TranscriptErrorFactory:
    return TranscriptErrorFactory(structlog.get_logger("test"))


@pytest.mark.unit
def test_read_turns_hit_parses_the_matched_session_file(tmp_path: Path) -> None:
    project_dir = tmp_path / "-home-user-workspace"
    project_dir.mkdir()
    (project_dir / "sess-1.jsonl").write_text(fx.user_env("hello") + "\n")
    repo = JsonlTranscriptRepository(str(tmp_path), _error_factory())

    transcript = repo.read_turns("sess-1", spawn_cwd="/home/user/workspace")

    assert transcript.available is True
    assert transcript.reason is None
    assert transcript.session_id == "sess-1"
    assert transcript.turns[0].text == "hello"


@pytest.mark.unit
def test_read_turns_miss_is_not_found(tmp_path: Path) -> None:
    repo = JsonlTranscriptRepository(str(tmp_path), _error_factory())
    transcript = repo.read_turns("no-such-session", spawn_cwd="/home/user/workspace")
    assert transcript.available is False
    assert transcript.reason == "not_found"
    assert transcript.turns == []


@pytest.mark.unit
def test_read_turns_hit_with_no_spawn_cwd_hint_resolves_the_single_match(tmp_path: Path) -> None:
    # The closed-lease path: the binding is released, so the hint is None —
    # the single glob match still resolves with no reconstructed cwd needed at all.
    project_dir = tmp_path / "-home-user-workspace"
    project_dir.mkdir()
    (project_dir / "sess-1.jsonl").write_text(fx.user_env("hello") + "\n")
    repo = JsonlTranscriptRepository(str(tmp_path), _error_factory())

    transcript = repo.read_turns("sess-1", spawn_cwd=None)

    assert transcript.available is True
    assert transcript.turns[0].text == "hello"


@pytest.mark.unit
def test_read_turns_multi_match_prefers_the_spawn_cwd_hint(tmp_path: Path) -> None:
    wanted_dir = tmp_path / "-home-user-workspace"
    other_dir = tmp_path / "-home-user-other"
    wanted_dir.mkdir()
    other_dir.mkdir()
    (wanted_dir / "sess-1.jsonl").write_text(fx.user_env("from wanted dir") + "\n")
    (other_dir / "sess-1.jsonl").write_text(fx.user_env("from other dir") + "\n")
    repo = JsonlTranscriptRepository(str(tmp_path), _error_factory())

    transcript = repo.read_turns("sess-1", spawn_cwd="/home/user/workspace")

    assert transcript.turns[0].text == "from wanted dir"


@pytest.mark.unit
def test_read_turns_multi_match_falls_back_to_newest_mtime_without_a_hint(tmp_path: Path) -> None:
    older_dir = tmp_path / "-home-user-older"
    newer_dir = tmp_path / "-home-user-newer"
    older_dir.mkdir()
    newer_dir.mkdir()
    older = older_dir / "sess-1.jsonl"
    newer = newer_dir / "sess-1.jsonl"
    older.write_text(fx.user_env("older") + "\n")
    newer.write_text(fx.user_env("newer") + "\n")
    now = time.time()
    os.utime(older, (now - 100, now - 100))
    os.utime(newer, (now, now))
    repo = JsonlTranscriptRepository(str(tmp_path), _error_factory())

    transcript = repo.read_turns("sess-1", spawn_cwd=None)

    assert transcript.turns[0].text == "newer"


@pytest.mark.unit
def test_read_turns_unreadable_file_degrades_to_unreadable_reason(tmp_path: Path) -> None:
    project_dir = tmp_path / "-home-user-workspace"
    project_dir.mkdir()
    # A directory named like the session file: stat().st_size / open() raise
    # IsADirectoryError (an OSError subclass) — a portable way to hit the unreadable
    # path without relying on permission semantics that differ when tests run as root.
    (project_dir / "sess-1.jsonl").mkdir()
    repo = JsonlTranscriptRepository(str(tmp_path), _error_factory())

    transcript = repo.read_turns("sess-1", spawn_cwd="/home/user/workspace")

    assert transcript.available is False
    assert transcript.reason == "unreadable"


class _TrackingFile:
    """Wraps a real file handle, recording every ``read()`` size — the rest of the
    file protocol (context manager, everything else) passes straight through."""

    def __init__(self, handle: Any) -> None:
        self._handle = handle
        self.read_sizes: list[int] = []

    def read(self, *args: Any, **kwargs: Any) -> bytes:
        data = self._handle.read(*args, **kwargs)
        self.read_sizes.append(len(data))
        return data

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)

    def __enter__(self) -> _TrackingFile:
        return self

    def __exit__(self, *exc: Any) -> None:
        self._handle.__exit__(*exc)


@pytest.mark.unit
def test_read_turns_tail_caps_a_pathological_file_and_flags_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The must-fix regression (issue #29 pre-push review, finding 1).

    A file far larger than the bound must never be read in full: peak memory is
    bounded by :data:`MAX_FILE_BYTES`, enforced by seeking to the tail before
    reading, not by reading everything and discarding the front. Pinned two ways —
    the parsed content is only the tail turns, and the actual bytes read off disk
    (via a wrapped ``Path.open``) never come close to the file's real size.
    """
    from blizzard.runner.transcripts.internal import jsonl_transcript_repository as repo_module

    lines = [fx.user_env(f"msg-{i}", uuid=f"u{i}") for i in range(2000)]
    line_bytes = max(len(line.encode("utf-8")) for line in lines)
    cap = line_bytes * 3  # room for a handful of lines, nowhere near the full file
    monkeypatch.setattr(repo_module, "MAX_FILE_BYTES", cap)

    project_dir = tmp_path / "-home-user-workspace"
    project_dir.mkdir()
    path = project_dir / "sess-1.jsonl"
    path.write_text("\n".join(lines) + "\n")
    file_size = path.stat().st_size
    assert file_size > cap * 10  # the file is genuinely pathological relative to the cap

    tracker = _TrackingFile
    tracked_files: list[_TrackingFile] = []
    real_open = Path.open

    def _tracking_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        handle = real_open(self, *args, **kwargs)
        if self == path:
            wrapped = tracker(handle)
            tracked_files.append(wrapped)
            return wrapped
        return handle

    monkeypatch.setattr(Path, "open", _tracking_open)

    repo = JsonlTranscriptRepository(str(tmp_path), _error_factory())
    transcript = repo.read_turns("sess-1", spawn_cwd="/home/user/workspace")

    assert transcript.available is True
    assert transcript.truncated is True
    assert transcript.turns[-1].text == "msg-1999"  # the newest line survived
    assert len(transcript.turns) < 10  # only a handful of tail lines fit the cap

    total_read = sum(size for f in tracked_files for size in f.read_sizes)
    assert total_read <= cap + line_bytes  # never read anywhere close to file_size
    assert total_read < file_size
