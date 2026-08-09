"""``transcripts/internal/segment_projection.py`` — hub segment turns onto the panel's
read model (blizzard#249, D5).

Constructs :class:`TurnSegmentView` fixtures directly, so this file's job is the
projection choice itself — narrowing and its count — not the transport (pinned in
``test_runner_archived_transcript_repository.py``)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.runner.transcripts.internal.segment_projection import select_turns, to_turn
from blizzard.wire.transcript_segment import SidechainSegmentView, ToolCallSegmentView, TurnSegmentView


def _turn(index: int, kind: str, *, text: str = "", sidechain: SidechainSegmentView | None = None) -> TurnSegmentView:
    return TurnSegmentView(
        index=index,
        kind=kind,
        timestamp=None,
        text=text,
        tool=None,
        thinking_redacted=False,
        sidechain=sidechain,
        truncated=False,
    )


@pytest.mark.unit
def test_env_and_asst_turns_are_kept_and_nothing_is_dropped() -> None:
    turns = [_turn(0, "env", text="build the thing"), _turn(1, "asst", text="Sure.")]
    kept, dropped_before = select_turns(turns)
    assert [t.kind for t in kept] == ["env", "asst"]
    assert sum(dropped_before) == 0
    assert [to_turn(t, i).text for i, t in enumerate(kept)] == ["build the thing", "Sure."]
    assert [to_turn(t, i).kind for i, t in enumerate(kept)] == ["env", "asst"]


@pytest.mark.unit
def test_a_thinking_turn_is_dropped_and_counted() -> None:
    turns = [_turn(0, "env", text="hi"), _turn(1, "thinking")]
    kept, dropped_before = select_turns(turns)
    assert [t.kind for t in kept] == ["env"]
    assert sum(dropped_before) == 1


@pytest.mark.unit
def test_a_sidechain_is_dropped_wholesale_but_its_spawning_tool_turn_is_kept() -> None:
    sidechain = SidechainSegmentView(
        agent_id="agent-1", agent_type="explorer", link="resolved", turns=[_turn(0, "asst", text="nested")]
    )
    tool_turn = TurnSegmentView(
        index=0,
        kind="tool",
        timestamp=None,
        text="",
        tool=ToolCallSegmentView(
            name="Task",
            input={},
            input_unparsed=None,
            input_shape="object",
            tool_use_id="t1",
            output="done",
            output_truncated=False,
        ),
        thinking_redacted=False,
        sidechain=sidechain,
        truncated=False,
    )
    kept, dropped_before = select_turns([tool_turn])
    assert [t.kind for t in kept] == ["tool"]
    assert sum(dropped_before) == 1  # the one nested turn, not the kept spawning call


@pytest.mark.unit
def test_a_sidechain_within_a_sidechain_counts_every_nested_turn() -> None:
    """The recursive case: a sidechain's own turn carries a further sidechain — every
    turn at every depth is dropped and counted, since the whole subagent conversation
    goes with the top-level drop."""
    inner = SidechainSegmentView(
        agent_id="agent-2", agent_type="explorer", link="resolved", turns=[_turn(0, "asst", text="deepest")]
    )
    inner_tool_turn = TurnSegmentView(
        index=0,
        kind="tool",
        timestamp=None,
        text="",
        tool=ToolCallSegmentView(
            name="Task",
            input={},
            input_unparsed=None,
            input_shape="object",
            tool_use_id="t2",
            output=None,
            output_truncated=False,
        ),
        thinking_redacted=False,
        sidechain=inner,
        truncated=False,
    )
    outer = SidechainSegmentView(agent_id="agent-1", agent_type="explorer", link="resolved", turns=[inner_tool_turn])
    outer_tool_turn = TurnSegmentView(
        index=0,
        kind="tool",
        timestamp=None,
        text="",
        tool=ToolCallSegmentView(
            name="Task",
            input={},
            input_unparsed=None,
            input_shape="object",
            tool_use_id="t1",
            output="done",
            output_truncated=False,
        ),
        thinking_redacted=False,
        sidechain=outer,
        truncated=False,
    )

    kept, dropped_before = select_turns([outer_tool_turn])

    assert [t.kind for t in kept] == ["tool"]
    assert sum(dropped_before) == 2  # the inner tool turn and the deepest asst turn


@pytest.mark.unit
def test_to_turn_serializes_tool_input_and_reads_the_timestamp() -> None:
    turn = TurnSegmentView(
        index=0,
        kind="tool",
        timestamp="2026-07-16T12:00:00+00:00",
        text="",
        tool=ToolCallSegmentView(
            name="Bash",
            input={"command": "ls"},
            input_unparsed=None,
            input_shape="object",
            tool_use_id="t1",
            output="file1",
            output_truncated=False,
        ),
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )

    result = to_turn(turn, 0)

    assert result.kind == "tool"
    assert result.tool_name == "Bash"
    assert result.tool_input == '{"command": "ls"}'
    assert result.tool_output == "file1"
    assert result.timestamp == datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


@pytest.mark.unit
def test_a_tool_turn_with_no_tool_payload_is_dropped_and_counted_not_raised() -> None:
    """review F1: a validating-but-inconsistent body (``kind="tool"`` with no ``tool``)
    degrades like ``thinking`` rather than hitting the old bare ``assert``."""
    turns = [_turn(0, "env", text="hi"), _turn(1, "tool")]
    kept, dropped_before = select_turns(turns)
    assert [t.kind for t in kept] == ["env"]
    assert sum(dropped_before) == 1


@pytest.mark.unit
def test_to_turn_degrades_a_tool_turn_with_no_payload_instead_of_raising() -> None:
    """Belt-and-suspenders with the above: ``to_turn`` itself is total, in case a future
    caller ever hands it a malformed turn ``select_turns`` didn't already filter."""
    turn = _turn(0, "tool", text="fallback text")
    result = to_turn(turn, 0)
    assert result.kind == "asst"
    assert result.text == "fallback text"


@pytest.mark.unit
def test_to_turn_reads_an_unparseable_timestamp_as_none_instead_of_raising() -> None:
    """review F1: a non-ISO ``timestamp`` degrades to ``None`` rather than raising
    ``ValueError`` past this 200-always seam."""
    turn = _turn(0, "env", text="hi")
    turn = turn.model_copy(update={"timestamp": "not-a-timestamp"})
    result = to_turn(turn, 0)
    assert result.timestamp is None
