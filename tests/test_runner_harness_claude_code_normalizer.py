"""``harness/internal/claude_code_normalizer.py`` (blizzard#245, phase 2).

All unit tier: :func:`normalize_lines` takes an iterable of strings and needs no
filesystem, mirroring ``tests/test_runner_transcripts.py``'s coverage of the parser
this module absorbs and extends — thinking turns, structured tool input, sidechain
assembly and its three record-level link routes, version stamps, and the widened
control skip list.
"""

from __future__ import annotations

import json

import pytest

from blizzard.runner.harness.internal import claude_code_normalizer as normalizer_module
from blizzard.runner.harness.internal.claude_code_normalizer import normalize_lines
from tests import transcript_fixtures as fx

# --------------------------------------------------------------------------- #
# Behavior ported unchanged from transcripts/parser.py
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_collapses_env_asst_and_tool_with_matched_output() -> None:
    lines = [
        fx.user_env("build the thing"),
        fx.assistant_text("Sure, I'll start."),
        fx.assistant_tool_use("t1", "Bash", {"command": "ls"}),
        fx.tool_result("t1", "file1\nfile2"),
    ]
    result = normalize_lines(lines)

    assert [t.kind for t in result.turns] == ["env", "asst", "tool"]
    assert result.turns[0].text == "build the thing"
    assert result.turns[1].text == "Sure, I'll start."
    tool = result.turns[2].tool
    assert tool is not None
    assert tool.name == "Bash"
    assert tool.output == "file1\nfile2"
    assert [t.index for t in result.turns] == [0, 1, 2]


@pytest.mark.unit
def test_one_assistant_record_yields_one_asst_and_n_tool_turns() -> None:
    # Two tool_use blocks in one record — built directly since the fixture emits
    # one block per call.
    content = [
        {"type": "text", "text": "Checking two things."},
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "a.py"}},
        {"type": "tool_use", "id": "t2", "name": "Read", "input": {"path": "b.py"}},
    ]
    line = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": content}, "uuid": "a1"})
    result = normalize_lines([line])

    assert [t.kind for t in result.turns] == ["asst", "tool", "tool"]
    assert result.turns[1].tool is not None and result.turns[1].tool.name == "Read"
    assert result.turns[2].tool is not None and result.turns[2].tool.name == "Read"


@pytest.mark.unit
def test_unmatched_tool_result_is_dropped() -> None:
    result = normalize_lines([fx.tool_result("no-such-id", "orphaned output")])
    assert result.turns == []


@pytest.mark.unit
def test_tool_turn_with_no_result_keeps_output_none() -> None:
    result = normalize_lines([fx.assistant_tool_use("t1", "Bash", {"command": "sleep 100"})])
    assert len(result.turns) == 1
    assert result.turns[0].kind == "tool"
    tool = result.turns[0].tool
    assert tool is not None
    assert tool.output is None  # renders "running…" — the live steady state


@pytest.mark.unit
def test_is_meta_record_is_filtered() -> None:
    assert normalize_lines([fx.meta_record()]).turns == []


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
        # New relative to the ported set (finding 5) — already inert, drop made explicit.
        "file-history-snapshot",
        "file-history-delta",
        "pr-link",
    ],
)
def test_control_records_are_filtered(record_type: str) -> None:
    assert normalize_lines([fx.control_record(record_type)]).turns == []


@pytest.mark.unit
def test_system_record_is_filtered() -> None:
    assert normalize_lines([fx.control_record("system")]).turns == []


@pytest.mark.unit
def test_ansi_escapes_are_stripped_from_text() -> None:
    result = normalize_lines([fx.ansi_text("hello")])
    assert result.turns[0].text == "hello"
    assert "\x1b" not in result.turns[0].text


@pytest.mark.unit
def test_private_mode_ansi_escapes_are_stripped_from_text() -> None:
    result = normalize_lines([fx.ansi_private_mode_text("hello")])
    assert result.turns[0].text == "hello"
    assert "\x1b" not in result.turns[0].text


@pytest.mark.unit
def test_malformed_and_truncated_records_degrade_to_fewer_turns_not_a_raise() -> None:
    lines = [fx.user_env("build the thing"), fx.truncated_line(), "not even json", "42", "null"]
    result = normalize_lines(lines)  # must not raise
    assert len(result.turns) == 1
    assert result.turns[0].text == "build the thing"


@pytest.mark.unit
def test_max_block_chars_caps_a_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(normalizer_module, "MAX_BLOCK_CHARS", 10)
    result = normalize_lines([fx.assistant_text("x" * 50)])

    assert len(result.turns[0].text) == 10
    assert result.turns[0].truncated is True  # block-level


# --------------------------------------------------------------------------- #
# New: thinking turns (finding 4)
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_redacted_thinking_is_the_expected_shape() -> None:
    """The corpus-normal case: empty `thinking` + a `signature` — presence, not prose."""
    result = normalize_lines([fx.thinking_block(text="", signature="sig-abc")])

    assert len(result.turns) == 1
    assert result.turns[0].kind == "thinking"
    assert result.turns[0].thinking_redacted is True
    assert result.turns[0].text == ""


@pytest.mark.unit
def test_present_thinking_text_is_not_flagged_redacted() -> None:
    result = normalize_lines([fx.thinking_block(text="reasoning about the fix", signature=None)])

    assert result.turns[0].kind == "thinking"
    assert result.turns[0].thinking_redacted is False
    assert result.turns[0].text == "reasoning about the fix"


# --------------------------------------------------------------------------- #
# New: structured tool input (never json.dumps'd)
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_tool_input_survives_as_a_mapping_never_a_json_string() -> None:
    result = normalize_lines([fx.assistant_tool_use("t1", "Read", {"path": "a.py", "limit": 10})])

    tool = result.turns[0].tool
    assert tool is not None
    assert tool.input == {"path": "a.py", "limit": 10}
    assert not isinstance(tool.input, str)
    assert tool.input_unparsed is None
    assert tool.input_shape == "object"


@pytest.mark.unit
def test_non_object_tool_input_is_kept_unparsed_not_coerced() -> None:
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "Weird", "input": "raw-string-input"}],
            },
            "uuid": "a1",
        }
    )
    result = normalize_lines([line])

    tool = result.turns[0].tool
    assert tool is not None
    assert tool.input == {}
    assert tool.input_unparsed == "raw-string-input"
    assert tool.input_shape == "string"


@pytest.mark.unit
def test_absent_tool_input_is_shaped_absent_not_object() -> None:
    """Distinct from an actual empty object (``input_shape == "object"``) — the
    re-materializing projection treats the two differently (blizzard#245 F10)."""
    line = json.dumps(
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash"}]},
            "uuid": "a1",
        }
    )
    result = normalize_lines([line])

    tool = result.turns[0].tool
    assert tool is not None
    assert tool.input == {}
    assert tool.input_unparsed is None
    assert tool.input_shape == "absent"


# --------------------------------------------------------------------------- #
# New: version stamps (finding 3)
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_harness_version_is_the_last_seen_record_version() -> None:
    lines = [
        fx.versioned(fx.user_env("hi"), version="2.1.209"),
        fx.versioned(fx.assistant_text("hello"), version="2.1.220"),
    ]
    result = normalize_lines(lines)
    assert result.harness_version == "2.1.220"


@pytest.mark.unit
def test_harness_version_is_none_when_no_record_carries_one() -> None:
    result = normalize_lines([fx.user_env("hi")])
    assert result.harness_version is None


# --------------------------------------------------------------------------- #
# New: sidechain link routes (blizzard#245)
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_agent_id_join_candidate_surfaces_for_a_tool_result_carrying_agent_id() -> None:
    """Route 1 (agent-id) is resolved by the sibling source module, not here — this
    normalizer only surfaces the candidate turn index, keyed by the agentId its
    `tool_result` carried (finding 2's exact join key)."""
    lines = [
        fx.assistant_tool_use("t1", "Task", {"subagent_type": "explorer", "prompt": "find X"}),
        fx.tool_result("t1", "subagent finished", agent_id="agent-abc"),
    ]
    result = normalize_lines(lines)

    assert len(result.turns) == 1
    assert result.agent_id_by_tool_turn == {0: "agent-abc"}
    assert result.turns[0].sidechain is None  # not resolved here — the source's job


@pytest.mark.unit
def test_uuid_chain_route_nests_the_inline_sidechain_under_its_spawning_tool_call() -> None:
    lines = [
        fx.assistant_tool_use(
            "t1",
            "Task",
            {"subagent_type": "explorer", "prompt": "go find X"},
            uuid="spawn-1",
            ts="2026-07-16T10:00:00Z",
        ),
        fx.sidechain_run_record("Starting exploration", uuid="sc1", parent_uuid="spawn-1", ts="2026-07-16T10:00:01Z"),
        fx.sidechain_run_record("Found X", uuid="sc2", parent_uuid="sc1", ts="2026-07-16T10:00:02Z"),
    ]
    result = normalize_lines(lines)

    assert result.unlinked_sidechains == []
    tool_turn = next(t for t in result.turns if t.kind == "tool")
    sidechain = tool_turn.sidechain
    assert sidechain is not None
    assert sidechain.link == "uuid-chain"
    assert sidechain.agent_type == "explorer"
    assert [t.text for t in sidechain.turns] == ["Starting exploration", "Found X"]


@pytest.mark.unit
def test_uuid_chain_route_falls_through_on_an_ambiguous_multi_tool_record() -> None:
    """A spawning record that emitted more than one tool call is ambiguous — the
    uuid-chain route must not guess which call a sidechain belongs to."""
    content = [
        {"type": "tool_use", "id": "t1", "name": "Task", "input": {"prompt": "a"}},
        {"type": "tool_use", "id": "t2", "name": "Task", "input": {"prompt": "b"}},
    ]
    spawn_line = json.dumps(
        {"type": "assistant", "message": {"role": "assistant", "content": content}, "uuid": "spawn-1"}
    )
    lines = [spawn_line, fx.sidechain_run_record("chatter", uuid="sc1", parent_uuid="spawn-1")]
    result = normalize_lines(lines)

    assert len(result.unlinked_sidechains) == 1
    assert result.unlinked_sidechains[0].link == "unlinked"


@pytest.mark.unit
def test_prompt_timestamp_route_resolves_when_the_uuid_chain_does_not() -> None:
    lines = [
        fx.assistant_tool_use(
            "t1",
            "Task",
            {"subagent_type": "explorer", "prompt": "explore Y"},
            uuid="spawn-2",
            ts="2026-07-16T10:00:00Z",
        ),
        fx.sidechain_run_record(
            "explore Y", uuid="sc3", parent_uuid="no-such-parent", role="user", ts="2026-07-16T10:00:05Z"
        ),
        fx.sidechain_run_record("working...", uuid="sc4", parent_uuid="sc3", ts="2026-07-16T10:00:06Z"),
    ]
    result = normalize_lines(lines)

    assert result.unlinked_sidechains == []
    tool_turn = next(t for t in result.turns if t.kind == "tool")
    sidechain = tool_turn.sidechain
    assert sidechain is not None
    assert sidechain.link == "prompt-timestamp"


@pytest.mark.unit
def test_prompt_timestamp_route_ignores_a_call_after_the_sidechain_started() -> None:
    """Only a *preceding* call can be this sidechain's spawn — a same-prompt call
    that started later is never mistaken for it."""
    lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "explore Y"}, uuid="spawn-late", ts="2026-07-16T11:00:00Z"),
        fx.sidechain_run_record(
            "explore Y", uuid="sc5", parent_uuid="no-such-parent", role="user", ts="2026-07-16T10:00:00Z"
        ),
    ]
    result = normalize_lines(lines)

    assert len(result.unlinked_sidechains) == 1
    assert result.unlinked_sidechains[0].link == "unlinked"


@pytest.mark.unit
def test_unresolvable_inline_sidechain_surfaces_unlinked_not_among_top_level_turns() -> None:
    """The deliberate rewrite of the old parser's `test_is_sidechain_record_is_filtered`
    (`plan-review:F5`): an isSidechain record used to yield zero turns, silently. The
    normalizer now *surfaces* it — still zero top-level turns, but present as data on
    `unlinked_sidechains` rather than dropped. The projection is what re-establishes the
    panel's zero-turn outcome, one layer down
    (`transcripts/internal/projected_transcript_repository.py`)."""
    result = normalize_lines([fx.sidechain_record()])

    assert result.turns == []
    assert len(result.unlinked_sidechains) == 1
    assert result.unlinked_sidechains[0].link == "unlinked"


# --------------------------------------------------------------------------- #
# New: sidecar-file normalization (is_sidechain_file=True)
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_sidecar_file_records_normalize_as_their_own_main_conversation() -> None:
    """Every record in a sidecar file carries `isSidechain: true` on itself
    (finding 1) — normalizing it with `is_sidechain_file=True` must treat that as
    the whole file's own conversation, not re-route it into a further sidechain
    bucket (which would silently produce zero turns)."""
    lines = [
        fx.sidecar_record("subagent starting", role="user", agent_id="agent-abc"),
        fx.sidecar_record("subagent done", role="assistant", agent_id="agent-abc"),
    ]
    result = normalize_lines(lines, is_sidechain_file=True)

    assert [t.kind for t in result.turns] == ["env", "asst"]
    assert result.unlinked_sidechains == []
