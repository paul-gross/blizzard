"""Per-kind turn extraction: main-lane recognition, sidechain depth/agent-type
attribution, and the dialect vocabulary's unknown-dialect fallback (blizzard#254,
Phase 2 — unit tier)."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.analytics.extraction import (
    KIND_AGENT_SPAWN,
    KIND_FILE_READ,
    KIND_SKILL_INVOCATION,
    extract_events,
)
from blizzard.wire.transcript_segment import SidechainSegmentView, ToolCallSegmentView, TurnSegmentView

pytestmark = pytest.mark.unit

_DIALECT = "claude-code-jsonl/2"


def _tool_turn(
    index: int, name: str, input: dict[str, object], *, timestamp: str | None = None, sidechain=None
) -> TurnSegmentView:
    return TurnSegmentView(
        index=index,
        kind="tool",
        timestamp=timestamp,
        text="",
        tool=ToolCallSegmentView(
            name=name,
            input=input,
            input_unparsed=None,
            input_shape="object",
            tool_use_id=f"t{index}",
            output=None,
            output_truncated=False,
        ),
        thinking_redacted=False,
        sidechain=sidechain,
        truncated=False,
    )


def _env_turn(index: int) -> TurnSegmentView:
    return TurnSegmentView(
        index=index,
        kind="env",
        timestamp=None,
        text="hello",
        tool=None,
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )


# --- main-lane recognition ---------------------------------------------------


def test_a_read_call_with_a_concrete_path_mints_a_file_read_event() -> None:
    turns = [_tool_turn(0, "Read", {"file_path": "src/a.py"}, timestamp="2026-08-12T10:00:00Z")]

    events = extract_events(turns, normalizer_version=_DIALECT)

    assert len(events) == 1
    event = events[0]
    assert event.kind == KIND_FILE_READ
    assert event.turn_path == "0"
    assert event.occurrence == 0
    assert event.payload == {"tool_name": "Read", "path": "src/a.py"}
    assert event.subject == "src/a.py"
    assert event.tool == "Read"
    assert event.depth == 0
    assert event.agent_type is None
    assert event.occurred_at is not None and event.occurred_at.isoformat() == "2026-08-12T10:00:00+00:00"


def test_a_grep_call_mints_no_file_read_event() -> None:
    """A pattern search is a different act (D5) — never a file_read."""
    turns = [_tool_turn(0, "Grep", {"pattern": "TODO"})]

    events = extract_events(turns, normalizer_version=_DIALECT)

    assert events == []


def test_a_skill_call_mints_a_skill_invocation_event() -> None:
    turns = [_tool_turn(0, "Skill", {"skill": "wf-commit"})]

    events = extract_events(turns, normalizer_version=_DIALECT)

    assert len(events) == 1
    assert events[0].kind == KIND_SKILL_INVOCATION
    assert events[0].payload == {"skill_name": "wf-commit"}
    assert events[0].subject == "wf-commit"
    assert events[0].tool == "Skill"


def test_an_agent_call_mints_an_agent_spawn_event() -> None:
    """The Claude Code harness names the subagent-spawn tool ``Agent`` (blizzard#327)."""
    turns = [_tool_turn(0, "Agent", {"subagent_type": "explorer", "prompt": "find X"})]

    events = extract_events(turns, normalizer_version=_DIALECT)

    assert len(events) == 1
    assert events[0].kind == KIND_AGENT_SPAWN
    assert events[0].payload == {"agent_type": "explorer"}
    assert events[0].subject == "explorer"
    assert events[0].tool == "Agent"


def test_an_agent_call_with_no_subagent_type_mints_no_event() -> None:
    turns = [_tool_turn(0, "Agent", {"prompt": "find X"})]

    events = extract_events(turns, normalizer_version=_DIALECT)

    assert events == []


@pytest.mark.parametrize("tool_name", ["TaskUpdate", "TaskCreate"])
def test_a_non_spawn_tool_call_mints_no_agent_spawn_event(tool_name: str) -> None:
    """Neighboring tool names never match the spawn gate (blizzard#327)."""
    turns = [_tool_turn(0, tool_name, {"subagent_type": "explorer"})]

    events = extract_events(turns, normalizer_version=_DIALECT)

    assert events == []


def test_an_env_turn_mints_nothing() -> None:
    events = extract_events([_env_turn(0)], normalizer_version=_DIALECT)

    assert events == []


# --- sidechain depth / nearest-enclosing agent type (D8) ---------------------


def test_a_linked_sidechain_turn_carries_depth_one_and_its_own_agent_type() -> None:
    inner = _tool_turn(0, "Read", {"file_path": "inner.py"})
    spawn = _tool_turn(
        0,
        "Agent",
        {"subagent_type": "explorer", "prompt": "find X"},
        sidechain=SidechainSegmentView(agent_id="a1", agent_type="explorer", link="uuid-chain", turns=[inner]),
    )

    events = extract_events([spawn], normalizer_version=_DIALECT)

    kinds = {e.kind: e for e in events}
    assert kinds[KIND_AGENT_SPAWN].depth == 0
    assert kinds[KIND_AGENT_SPAWN].agent_type is None
    assert kinds[KIND_FILE_READ].depth == 1
    assert kinds[KIND_FILE_READ].agent_type == "explorer"
    assert kinds[KIND_FILE_READ].turn_path == "0.0"


def test_an_agent_call_nested_in_a_sidechain_mints_an_agent_spawn_event_at_depth_one() -> None:
    """A spawn recognized from inside a sidechain, not just the main lane (blizzard#327)."""
    nested_spawn = _tool_turn(0, "Agent", {"subagent_type": "coder", "prompt": "implement"})
    outer_spawn = _tool_turn(
        0,
        "Agent",
        {"subagent_type": "explorer", "prompt": "find X"},
        sidechain=SidechainSegmentView(agent_id="a1", agent_type="explorer", link="uuid-chain", turns=[nested_spawn]),
    )

    events = extract_events([outer_spawn], normalizer_version=_DIALECT)

    spawns = [e for e in events if e.kind == KIND_AGENT_SPAWN]
    assert len(spawns) == 2
    nested = next(e for e in spawns if e.turn_path == "0.0")
    assert nested.depth == 1
    assert nested.agent_type == "explorer"
    assert nested.subject == "coder"


def test_a_nested_sidechain_turn_carries_depth_two_and_the_nearest_enclosing_agent_type() -> None:
    """A depth-2 read: the inner sidechain's own agent type wins, not the outer one's."""
    innermost = _tool_turn(0, "Read", {"file_path": "deep.py"})
    middle_spawn = _tool_turn(
        0,
        "Agent",
        {"subagent_type": "coder", "prompt": "implement"},
        sidechain=SidechainSegmentView(agent_id="a2", agent_type="coder", turns=[innermost], link="uuid-chain"),
    )
    outer_spawn = _tool_turn(
        0,
        "Agent",
        {"subagent_type": "reviewer", "prompt": "review"},
        sidechain=SidechainSegmentView(agent_id="a1", agent_type="reviewer", turns=[middle_spawn], link="uuid-chain"),
    )

    events = extract_events([outer_spawn], normalizer_version=_DIALECT)

    reads = [e for e in events if e.kind == KIND_FILE_READ]
    assert len(reads) == 1
    assert reads[0].depth == 2
    assert reads[0].agent_type == "coder"
    assert reads[0].turn_path == "0.0.0"


def test_an_unresolved_sidechain_turn_carries_depth_but_no_agent_type() -> None:
    """D8: an unresolved link's own agent type is honestly ``None`` — never borrowed
    from an ancestor sidechain."""
    inner = _tool_turn(0, "Read", {"file_path": "orphan.py"})
    spawn = _tool_turn(
        0,
        "Agent",
        {"subagent_type": "explorer", "prompt": "find X"},
        sidechain=SidechainSegmentView(agent_id=None, agent_type=None, link="unlinked", turns=[inner]),
    )

    events = extract_events([spawn], normalizer_version=_DIALECT)

    reads = [e for e in events if e.kind == KIND_FILE_READ]
    assert len(reads) == 1
    assert reads[0].depth == 1
    assert reads[0].agent_type is None


# --- dialect vocabulary (D9) --------------------------------------------------


def test_an_unknown_dialect_derives_zero_events() -> None:
    turns = [
        _tool_turn(0, "Read", {"file_path": "a.py"}),
        _tool_turn(1, "Skill", {"skill": "wf-commit"}),
        _tool_turn(2, "Agent", {"subagent_type": "explorer"}),
    ]

    events = extract_events(turns, normalizer_version="some-other-harness/1")

    assert events == []
