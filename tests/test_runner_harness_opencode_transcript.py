"""``harness/internal/opencode_transcript_source.py`` — unit tier, hermetic: a scripted
:class:`IOpenCodeExporter`, never a real ``opencode`` binary. Covers cold/forward identity
reads, the malformed-cursor/export-failure ``unreadable`` paths, child-session sidechain
linking, and ``read_raw_lines``'s round trip through ``OpenCodeAdapter.sum_transcript_usage``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs

from blizzard.runner.harness.internal.offline_compatibility import admitted_corpus_versions
from blizzard.runner.harness.internal.opencode_adapter import OpenCodeAdapter
from blizzard.runner.harness.internal.opencode_export import IOpenCodeExporter, OpenCodeExportError
from blizzard.runner.harness.internal.opencode_normalizer import NORMALIZER_VERSION, build_turns
from blizzard.runner.harness.internal.opencode_probe import ADMITTED_OPENCODE_RANGE
from blizzard.runner.harness.internal.opencode_shapes import parse_session_export
from blizzard.runner.harness.internal.opencode_transcript_source import OpenCodeTranscriptSource
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.harness.transcript import TranscriptErrorFactory, TranscriptPosition
from tests.runner_fakes import FakeProbe

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "src" / "blizzard" / "runner" / "harness"
# The oldest committed corpus inside the admitted range (blizzard#438) — stays correct as corpora are added.
_AN_ADMITTED_OPENCODE_VERSION = admitted_corpus_versions("opencode", ADMITTED_OPENCODE_RANGE)[0]
_CORPUS_DIR = _PACKAGE_ROOT / "contracts" / "opencode" / _AN_ADMITTED_OPENCODE_VERSION


def _error_factory() -> TranscriptErrorFactory:
    return TranscriptErrorFactory(structlog.get_logger("test"))


def _load_contract(name: str) -> dict[str, Any]:
    return json.loads((_CORPUS_DIR / name).read_text())["export"]


class FakeExporter:
    """A scripted :class:`IOpenCodeExporter`: each session id maps to a JSON string to
    return, or an exception instance to raise — mutable, so a test can grow a session
    between two reads the way a live ``opencode export`` would."""

    def __init__(self, scripts: dict[str, str | Exception] | None = None) -> None:
        self.scripts = dict(scripts or {})
        self.calls: list[str] = []

    def export(self, session_id: str) -> str:
        self.calls.append(session_id)
        script = self.scripts.get(session_id)
        if script is None:
            raise OpenCodeExportError(f"no script for {session_id!r}")
        if isinstance(script, Exception):
            raise script
        return script


def _conforms(x: FakeExporter) -> IOpenCodeExporter:
    return x


# --- export fixture builders (contracts/opencode/1.18.25/child_session.json's own shapes) ---


def _session_info(session_id: str, *, version: str = "1.18.25", parent_id: str | None = None) -> dict[str, Any]:
    info: dict[str, Any] = {"id": session_id, "version": version}
    if parent_id is not None:
        info["parentID"] = parent_id
    return info


def _text_part(session_id: str, message_id: str, part_id: str, text: str) -> dict[str, Any]:
    return {"id": part_id, "sessionID": session_id, "messageID": message_id, "type": "text", "text": text}


def _reasoning_part(session_id: str, message_id: str, part_id: str, text: str = "") -> dict[str, Any]:
    return {"id": part_id, "sessionID": session_id, "messageID": message_id, "type": "reasoning", "text": text}


def _tool_part(
    session_id: str,
    message_id: str,
    part_id: str,
    *,
    call_id: str,
    tool: str = "bash",
    status: str = "completed",
    input: dict[str, Any] | None = None,
    output: str | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {"status": status, "input": input or {}}
    if output is not None:
        state["output"] = output
    if error is not None:
        state["error"] = error
    if metadata is not None:
        state["metadata"] = metadata
    if title is not None:
        state["title"] = title
    return {
        "id": part_id,
        "sessionID": session_id,
        "messageID": message_id,
        "type": "tool",
        "callID": call_id,
        "tool": tool,
        "state": state,
    }


def _step_finish_part(session_id: str, message_id: str, part_id: str, *, input_tokens: int = 10) -> dict[str, Any]:
    return {
        "id": part_id,
        "sessionID": session_id,
        "messageID": message_id,
        "type": "step-finish",
        "reason": "stop",
        "cost": 0,
        "tokens": {"input": input_tokens, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}},
    }


def _user_message(session_id: str, message_id: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"info": {"id": message_id, "sessionID": session_id, "role": "user"}, "parts": parts}


def _assistant_message(session_id: str, message_id: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"info": {"id": message_id, "sessionID": session_id, "role": "assistant"}, "parts": parts}


def _export(session_id: str, messages: list[dict[str, Any]], **info_kwargs: Any) -> str:
    return json.dumps({"info": _session_info(session_id, **info_kwargs), "messages": messages})


# --- cold `turns_since` ---


@pytest.mark.unit
def test_cold_turns_since_produces_env_asst_thinking_tool_turns() -> None:
    export = _export(
        "sess-1",
        [
            _user_message("sess-1", "m-user", [_text_part("sess-1", "m-user", "p-user", "hello")]),
            _assistant_message(
                "sess-1",
                "m-asst",
                [
                    _reasoning_part("sess-1", "m-asst", "p-think"),
                    _text_part("sess-1", "m-asst", "p-text", "hi there"),
                    _tool_part(
                        "sess-1", "m-asst", "p-tool", call_id="call-1", input={"cmd": "ls"}, output="file1\nfile2"
                    ),
                ],
            ),
        ],
    )
    source = OpenCodeTranscriptSource(FakeExporter({"sess-1": export}), _error_factory())

    batch = source.turns_since("sess-1", spawn_cwd=None, since=None)

    assert batch.available is True
    assert batch.reason is None
    assert [t.kind for t in batch.turns] == ["env", "thinking", "asst", "tool"]
    assert batch.turns[0].text == "hello"
    assert batch.turns[1].thinking_redacted is True
    assert batch.turns[1].text == ""
    assert batch.turns[2].text == "hi there"
    tool = batch.turns[3].tool
    assert tool is not None
    assert tool.name == "bash"
    assert tool.input == {"cmd": "ls"}
    assert tool.input_unparsed is None
    assert tool.input_shape == "object"
    assert tool.tool_use_id == "call-1"
    assert tool.output == "file1\nfile2"
    assert tool.output_truncated is False
    assert batch.normalizer_version == NORMALIZER_VERSION
    assert batch.harness_version == "1.18.25"
    assert batch.complete is True
    assert batch.truncated is False
    assert batch.sidechain_truncated is False
    assert batch.unlinked_sidechains == []
    assert batch.next_position is not None


@pytest.mark.unit
def test_cold_turns_since_tolerates_an_empty_tool_title() -> None:
    export = _export(
        "sess-1",
        [
            _user_message("sess-1", "m-user", [_text_part("sess-1", "m-user", "p-user", "hello")]),
            _assistant_message(
                "sess-1",
                "m-asst",
                [
                    _tool_part(
                        "sess-1",
                        "m-asst",
                        "p-tool",
                        call_id="call-1",
                        tool="glob",
                        input={"pattern": "*.missing"},
                        output="",
                        title="",
                    ),
                ],
            ),
        ],
    )
    source = OpenCodeTranscriptSource(FakeExporter({"sess-1": export}), _error_factory())

    batch = source.turns_since("sess-1", spawn_cwd=None, since=None)

    assert batch.available is True
    assert [t.kind for t in batch.turns] == ["env", "tool"]
    tool = batch.turns[1].tool
    assert tool is not None
    assert tool.name == "glob"
    assert tool.tool_use_id == "call-1"


@pytest.mark.unit
def test_forward_turns_since_only_reemits_the_identity_that_changed() -> None:
    """review F3: a pending tool part completing on a LATER read must not re-emit the whole
    call as a second, full turn — the cursor admits it as ``"updated"``, and that ships a
    ``LateToolOutput`` patch instead, the same shape Claude Code's own late-output case uses."""
    exporter = FakeExporter(
        {
            "sess-1": _export(
                "sess-1",
                [
                    _user_message("sess-1", "m-user", [_text_part("sess-1", "m-user", "p-user", "go")]),
                    _assistant_message(
                        "sess-1",
                        "m-asst",
                        [_tool_part("sess-1", "m-asst", "p-tool", call_id="call-1", status="pending")],
                    ),
                ],
            )
        }
    )
    source = OpenCodeTranscriptSource(exporter, _error_factory())
    first = source.turns_since("sess-1", spawn_cwd=None, since=None)
    assert first.turns[-1].tool is not None
    assert first.turns[-1].tool.output is None
    assert first.late_tool_outputs == []

    exporter.scripts["sess-1"] = _export(
        "sess-1",
        [
            _user_message("sess-1", "m-user", [_text_part("sess-1", "m-user", "p-user", "go")]),
            _assistant_message(
                "sess-1",
                "m-asst",
                [_tool_part("sess-1", "m-asst", "p-tool", call_id="call-1", status="completed", output="done")],
            ),
        ],
    )
    second = source.turns_since("sess-1", spawn_cwd=None, since=first.next_position)

    # No duplicate turn — the completion patches the earlier call instead.
    assert second.turns == []
    [patch] = second.late_tool_outputs
    assert patch.tool_use_id == "call-1"
    assert patch.output == "done"
    assert patch.output_truncated is False

    # A THIRD read from the same tail sees nothing new at all: the identity's fingerprint
    # hasn't changed again, so it is neither re-built nor re-patched (never re-admitted).
    third = source.turns_since("sess-1", spawn_cwd=None, since=second.next_position)
    assert third.turns == []
    assert third.late_tool_outputs == []


@pytest.mark.unit
def test_a_tool_part_updated_with_no_output_yet_ships_nothing() -> None:
    """review F3: pending -> running carries no output to patch, so the revision is simply
    not shipped — not a duplicate turn, not an empty patch."""
    exporter = FakeExporter(
        {
            "sess-1": _export(
                "sess-1",
                [
                    _assistant_message(
                        "sess-1",
                        "m-asst",
                        [_tool_part("sess-1", "m-asst", "p-tool", call_id="call-1", status="pending")],
                    )
                ],
            )
        }
    )
    source = OpenCodeTranscriptSource(exporter, _error_factory())
    first = source.turns_since("sess-1", spawn_cwd=None, since=None)

    exporter.scripts["sess-1"] = _export(
        "sess-1",
        [
            _assistant_message(
                "sess-1", "m-asst", [_tool_part("sess-1", "m-asst", "p-tool", call_id="call-1", status="running")]
            )
        ],
    )
    second = source.turns_since("sess-1", spawn_cwd=None, since=first.next_position)

    assert second.turns == []
    assert second.late_tool_outputs == []


# --- malformed cursor / export failure -> `unreadable`, never a silent reset ---


@pytest.mark.unit
def test_malformed_cursor_token_is_unreadable_not_a_silent_reset() -> None:
    exporter = FakeExporter({"sess-1": _export("sess-1", [])})
    source = OpenCodeTranscriptSource(exporter, _error_factory())

    with capture_logs() as logs:
        batch = source.turns_since("sess-1", spawn_cwd=None, since=TranscriptPosition(token="not json"))

    assert batch.available is False
    assert batch.reason == "unreadable"
    assert exporter.calls == []  # never even attempted the export once the cursor was bad
    assert any(entry["log_level"] == "error" for entry in logs)


@pytest.mark.unit
def test_export_failure_is_unreadable() -> None:
    exporter = FakeExporter({"sess-1": OpenCodeExportError("no such session")})
    source = OpenCodeTranscriptSource(exporter, _error_factory())

    with capture_logs() as logs:
        batch = source.turns_since("sess-1", spawn_cwd=None, since=None)

    assert batch.available is False
    assert batch.reason == "unreadable"
    assert any(entry["log_level"] == "error" for entry in logs)


@pytest.mark.unit
def test_malformed_export_shape_is_unreadable() -> None:
    exporter = FakeExporter({"sess-1": "{}"})  # missing required `info`/`messages`
    source = OpenCodeTranscriptSource(exporter, _error_factory())

    batch = source.turns_since("sess-1", spawn_cwd=None, since=None)

    assert batch.available is False
    assert batch.reason == "unreadable"


# --- child-session sidechain linking ---


@pytest.mark.unit
def test_a_linked_child_session_attaches_as_a_sidechain() -> None:
    root = _export(
        "root-1",
        [
            _assistant_message(
                "root-1",
                "m-asst",
                [
                    _tool_part(
                        "root-1",
                        "m-asst",
                        "p-task",
                        call_id="call-task",
                        tool="task",
                        input={"agent": "explorer"},
                        output="done",
                        metadata={"sessionID": "child-1"},
                    )
                ],
            )
        ],
    )
    child = _export(
        "child-1",
        [_user_message("child-1", "m-child-user", [_text_part("child-1", "m-child-user", "p", "dig in")])],
        parent_id="root-1",
    )
    source = OpenCodeTranscriptSource(FakeExporter({"root-1": root, "child-1": child}), _error_factory())

    batch = source.turns_since("root-1", spawn_cwd=None, since=None)

    tool_turn = batch.turns[0]
    assert tool_turn.sidechain is not None
    assert tool_turn.sidechain.agent_id == "child-1"
    assert tool_turn.sidechain.agent_type == "explorer"
    assert tool_turn.sidechain.link == "session-id"
    assert [t.text for t in tool_turn.sidechain.turns] == ["dig in"]
    assert batch.unlinked_sidechains == []


def _root_with_child_candidate(child_session_id: str = "child-1") -> str:
    return _export(
        "root-1",
        [
            _assistant_message(
                "root-1",
                "m-asst",
                [
                    _tool_part(
                        "root-1",
                        "m-asst",
                        "p-task",
                        call_id="call-task",
                        tool="task",
                        output="done",
                        metadata={"sessionID": child_session_id},
                    )
                ],
            )
        ],
    )


@pytest.mark.unit
def test_a_child_export_whose_parent_id_mismatches_surfaces_as_unlinked_and_is_retried() -> None:
    """review F4: a momentary `parentID` mismatch is visible (not lost) and retried on a
    later tick, without ever violating the cursor's own never-re-admit contract for the
    already-shipped spawning tool part."""
    root = _root_with_child_candidate()
    exporter = FakeExporter({"root-1": root, "child-1": _export("child-1", [], parent_id="some-other-root")})
    source = OpenCodeTranscriptSource(exporter, _error_factory())

    with capture_logs() as logs:
        first = source.turns_since("root-1", spawn_cwd=None, since=None)

    assert first.turns[0].sidechain is None
    # An expected "not this session's own child" case — never logged as a failure.
    assert not any(entry["log_level"] == "warning" for entry in logs)
    [unresolved] = first.unlinked_sidechains
    assert unresolved.agent_id == "child-1"
    assert unresolved.turns == []
    assert first.agent_tool_use_ids == {}  # not yet a confirmed pair — nothing to link by

    # The mismatch resolves on a later tick: the child's own `parentID` now names `root-1`.
    exporter.scripts["child-1"] = _export(
        "child-1",
        [_user_message("child-1", "m-child-user", [_text_part("child-1", "m-child-user", "p", "dig in")])],
        parent_id="root-1",
    )
    second = source.turns_since("root-1", spawn_cwd=None, since=first.next_position)

    assert second.turns == []  # the spawning tool part already shipped — no turn to rebuild
    [linked] = second.unlinked_sidechains
    assert linked.agent_id == "child-1"
    assert [t.text for t in linked.turns] == ["dig in"]
    assert second.agent_tool_use_ids == {"child-1": "call-task"}

    # Once linked, a later tick never refetches or re-surfaces it.
    calls_before = list(exporter.calls)
    third = source.turns_since("root-1", spawn_cwd=None, since=second.next_position)
    assert third.unlinked_sidechains == []
    assert exporter.calls == [*calls_before, "root-1"]  # no further `child-1` export attempted


@pytest.mark.unit
def test_a_child_export_that_fails_once_then_succeeds() -> None:
    """review F4: an export failure is visible (not lost) and picked up once the child
    becomes readable, rather than a permanent, silent loss."""
    root = _root_with_child_candidate()
    exporter = FakeExporter({"root-1": root, "child-1": OpenCodeExportError("gone")})
    source = OpenCodeTranscriptSource(exporter, _error_factory())

    with capture_logs() as logs:
        first = source.turns_since("root-1", spawn_cwd=None, since=None)

    assert first.turns[0].sidechain is None
    assert any(entry["log_level"] == "warning" for entry in logs)
    [unresolved] = first.unlinked_sidechains
    assert unresolved.agent_id == "child-1"
    assert unresolved.turns == []

    exporter.scripts["child-1"] = _export(
        "child-1",
        [_user_message("child-1", "m-child-user", [_text_part("child-1", "m-child-user", "p", "dig in")])],
        parent_id="root-1",
    )
    second = source.turns_since("root-1", spawn_cwd=None, since=first.next_position)

    assert second.turns == []
    [linked] = second.unlinked_sidechains
    assert linked.agent_id == "child-1"
    assert linked.agent_type is None
    assert [t.text for t in linked.turns] == ["dig in"]
    assert second.agent_tool_use_ids == {"child-1": "call-task"}


# --- `read_raw_lines` — usage-bearing assistant messages, round-tripped through the adapter ---


@pytest.mark.unit
def test_read_raw_lines_returns_the_range_and_round_trips_through_the_adapter() -> None:
    exporter = FakeExporter(
        {
            "sess-1": _export(
                "sess-1",
                [
                    _user_message("sess-1", "m-u1", [_text_part("sess-1", "m-u1", "p", "go")]),
                    _assistant_message(
                        "sess-1", "m-a1", [_step_finish_part("sess-1", "m-a1", "p-fin1", input_tokens=10)]
                    ),
                ],
            )
        }
    )
    source = OpenCodeTranscriptSource(exporter, _error_factory())
    start = source.tail_position("sess-1", spawn_cwd=None)
    assert start is not None

    exporter.scripts["sess-1"] = _export(
        "sess-1",
        [
            _user_message("sess-1", "m-u1", [_text_part("sess-1", "m-u1", "p-u1", "go")]),
            _assistant_message("sess-1", "m-a1", [_step_finish_part("sess-1", "m-a1", "p-fin1", input_tokens=10)]),
            _user_message("sess-1", "m-u2", [_text_part("sess-1", "m-u2", "p-u2", "again")]),
            _assistant_message("sess-1", "m-a2", [_step_finish_part("sess-1", "m-a2", "p-fin2", input_tokens=20)]),
        ],
    )
    end = source.tail_position("sess-1", spawn_cwd=None)
    assert end is not None

    lines = source.read_raw_lines("sess-1", spawn_cwd=None, start=start, end=end)

    assert len(lines) == 1
    decoded = json.loads(lines[0])
    assert decoded["info"]["id"] == "m-a2"

    process = FakeProbe()
    adapter = OpenCodeAdapter(binary="opencode", process=process, launcher=ProcessLauncher(process))
    usage = adapter.sum_transcript_usage(lines, "resume")
    assert usage.input_tokens == 20


@pytest.mark.unit
def test_read_raw_lines_reuses_the_export_a_preceding_tail_position_just_fetched() -> None:
    """review F17: `tail_position` immediately followed by `read_raw_lines` for the same
    session — `_worker_sample`'s own shape — costs one real export, not two."""
    exporter = FakeExporter(
        {
            "sess-1": _export(
                "sess-1",
                [_assistant_message("sess-1", "m-a1", [_step_finish_part("sess-1", "m-a1", "p-fin1")])],
            )
        }
    )
    source = OpenCodeTranscriptSource(exporter, _error_factory())

    end = source.tail_position("sess-1", spawn_cwd=None)
    assert exporter.calls == ["sess-1"]

    lines = source.read_raw_lines("sess-1", spawn_cwd=None, start=None, end=end)

    assert len(lines) == 1  # the memoized read still saw the real content
    assert exporter.calls == ["sess-1"]  # no second export for the same session


@pytest.mark.unit
def test_read_raw_lines_still_fetches_fresh_for_a_different_session_than_the_memo() -> None:
    """The memo is keyed by session id — a `tail_position` for one session never leaks into
    a `read_raw_lines` for another."""
    exporter = FakeExporter(
        {
            "sess-1": _export("sess-1", []),
            "sess-2": _export(
                "sess-2",
                [_assistant_message("sess-2", "m-a1", [_step_finish_part("sess-2", "m-a1", "p-fin1")])],
            ),
        }
    )
    source = OpenCodeTranscriptSource(exporter, _error_factory())

    source.tail_position("sess-1", spawn_cwd=None)
    lines = source.read_raw_lines("sess-2", spawn_cwd=None, start=None, end=None)

    assert len(lines) == 1
    assert exporter.calls == ["sess-1", "sess-2"]


@pytest.mark.unit
def test_tail_position_never_reuses_a_stale_memo_even_when_one_is_available() -> None:
    """`tail_position` is a forward-read anchor — it must always be genuinely fresh, never
    one call stale, even though it keeps refreshing the memo for others to consume."""
    exporter = FakeExporter(
        {"sess-1": _export("sess-1", [_user_message("sess-1", "m1", [_text_part("sess-1", "m1", "p1", "go")])])}
    )
    source = OpenCodeTranscriptSource(exporter, _error_factory())
    first = source.tail_position("sess-1", spawn_cwd=None)

    exporter.scripts["sess-1"] = _export(
        "sess-1",
        [
            _user_message("sess-1", "m1", [_text_part("sess-1", "m1", "p1", "go")]),
            _user_message("sess-1", "m2", [_text_part("sess-1", "m2", "p2", "again")]),
        ],
    )
    second = source.tail_position("sess-1", spawn_cwd=None)

    assert second != first  # reflects the mutation, not a memoized copy of the first read
    assert exporter.calls == ["sess-1", "sess-1"]


@pytest.mark.unit
def test_read_raw_lines_with_no_range_returns_every_usage_bearing_message() -> None:
    exporter = FakeExporter(
        {
            "sess-1": _export(
                "sess-1",
                [
                    _assistant_message(
                        "sess-1", "m-a1", [_step_finish_part("sess-1", "m-a1", "p-fin1", input_tokens=10)]
                    ),
                    _assistant_message(
                        "sess-1", "m-a2", [_step_finish_part("sess-1", "m-a2", "p-fin2", input_tokens=20)]
                    ),
                ],
            )
        }
    )
    source = OpenCodeTranscriptSource(exporter, _error_factory())

    lines = source.read_raw_lines("sess-1", spawn_cwd=None, start=None, end=None)

    assert len(lines) == 2


@pytest.mark.unit
def test_read_raw_lines_malformed_range_token_is_empty_not_raised() -> None:
    exporter = FakeExporter({"sess-1": _export("sess-1", [])})
    source = OpenCodeTranscriptSource(exporter, _error_factory())

    lines = source.read_raw_lines("sess-1", spawn_cwd=None, start=TranscriptPosition(token="not json"), end=None)

    assert lines == []


@pytest.mark.unit
def test_read_raw_lines_export_failure_is_empty() -> None:
    source = OpenCodeTranscriptSource(FakeExporter({"sess-1": OpenCodeExportError("gone")}), _error_factory())

    assert source.read_raw_lines("sess-1", spawn_cwd=None) == []


# --- `tail_position` / `size_bytes` / `context_tokens` ---


@pytest.mark.unit
def test_tail_position_on_an_unreadable_session_is_none() -> None:
    source = OpenCodeTranscriptSource(FakeExporter({"sess-1": OpenCodeExportError("gone")}), _error_factory())

    assert source.tail_position("sess-1", spawn_cwd=None) is None


@pytest.mark.unit
def test_size_bytes_equals_the_raw_exporter_outputs_byte_length() -> None:
    raw = _export("sess-1", [])
    source = OpenCodeTranscriptSource(FakeExporter({"sess-1": raw}), _error_factory())

    assert source.size_bytes("sess-1", spawn_cwd=None) == len(raw.encode("utf-8"))


@pytest.mark.unit
def test_size_bytes_on_export_failure_is_none() -> None:
    source = OpenCodeTranscriptSource(FakeExporter({"sess-1": OpenCodeExportError("gone")}), _error_factory())

    assert source.size_bytes("sess-1", spawn_cwd=None) is None


@pytest.mark.unit
def test_context_tokens_is_unconditionally_none() -> None:
    source = OpenCodeTranscriptSource(FakeExporter({"sess-1": _export("sess-1", [])}), _error_factory())

    assert source.context_tokens("sess-1", spawn_cwd=None) is None


# --- pinned corpus (review F23): the real fixtures, not only hand-built ones ---


@pytest.mark.unit
def test_pinned_compaction_export_never_turns_the_compaction_or_step_finish_parts() -> None:
    """The real ``contracts/opencode/1.18.25/compaction.json`` export, not a hand-built
    stand-in: a ``compaction`` part and a ``step-finish`` part never produce a turn on their
    own — only the message's own ``text`` part does."""
    export = parse_session_export(_load_contract("compaction.json"))

    turns, _ = build_turns(export.messages, admitted=None)

    assert [t.kind for t in turns] == ["env", "asst"]
    assert turns[0].text == "Earlier retained context."
    assert turns[1].text == "The retained context is compacted. <Choice>pass</Choice>"


@pytest.mark.unit
def test_pinned_compaction_export_through_the_source_never_ships_a_compaction_turn() -> None:
    """The same claim, proven through the source's own ``turns_since`` rather than
    ``build_turns`` alone — the seam a real pump reads."""
    raw = json.dumps(_load_contract("compaction.json"))
    source = OpenCodeTranscriptSource(FakeExporter({"ses_compaction": raw}), _error_factory())

    batch = source.turns_since("ses_compaction", spawn_cwd=None, since=None)

    assert [t.kind for t in batch.turns] == ["env", "asst"]
    assert batch.harness_version == "1.18.25"
