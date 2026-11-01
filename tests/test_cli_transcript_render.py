"""The shared terminal rendering of a transcript read (unit tier), by value over wire-shaped bodies."""

from __future__ import annotations

import pytest

from blizzard.cli.transcript_render import SegmentContentLines, SegmentIndexLines
from tests.transcript_bodies import content_body, index_body, tool, turn

pytestmark = pytest.mark.unit


def _lines(body: dict[str, object]) -> list[str]:
    return list(SegmentContentLines(body).lines())


def test_a_segment_reads_turn_by_turn_with_a_late_result_folded_onto_its_call() -> None:
    assert _lines(content_body()) == [
        "sg_1  final",
        "2026-10-09T12:00:00Z  env",
        "  you are a worker",
        "2026-10-09T12:00:01Z  assistant",
        "  listing the files",
        "2026-10-09T12:00:02Z  thinking",
        "  redacted",
        '2026-10-09T12:00:03Z  tool Bash  {"command": "ls"}',
        "  a.txt",
        "  b.txt",
    ]


def test_a_call_with_no_result_yet_reads_as_running() -> None:
    body = content_body()
    body["turns"] = [turn(0, "tool", tool=tool("Bash", "tu_1", None))]

    assert _lines(body)[-1] == "  running…"


def test_an_unredacted_thinking_turn_shows_its_text_and_a_truncated_turn_is_noted() -> None:
    body = content_body()
    body["turns"] = [turn(0, "thinking", "weighing it", truncated=True)]

    assert _lines(body)[1:] == ["2026-10-09T12:00:00Z  thinking", "  weighing it", "  ⋯ truncated"]


def test_a_turn_with_no_timestamp_shows_a_dash() -> None:
    body = content_body()
    body["turns"] = [turn(0, "asst", "hi", timestamp=None)]

    assert _lines(body)[1] == "—  assistant"


def test_a_long_input_is_cut_to_the_boards_preview_length() -> None:
    body = content_body()
    body["turns"] = [turn(0, "tool", tool=tool("Write", None, "ok", input_shape="text", input_unparsed="x" * 400))]

    assert _lines(body)[1] == f"2026-10-09T12:00:00Z  tool Write  {'x' * 300}…"


def test_a_subagents_turns_are_indented_under_the_call_that_spawned_it() -> None:
    inner = {
        "agent_id": "ag_1",
        "agent_type": "explorer",
        "link": "linked",
        "turns": [turn(0, "asst", "found it")],
        "parent_tool_use_id": None,
    }
    body = content_body()
    body["turns"] = [turn(0, "tool", tool=tool("Task", "tu_9", "done"), sidechain=inner)]

    assert _lines(body)[2:] == [
        "  done",
        "  sidechain explorer",
        "    2026-10-09T12:00:00Z  assistant",
        "      found it",
    ]


def test_a_late_sidechain_nests_under_its_parent_call_and_an_orphan_stays_top_level() -> None:
    late = {
        "agent_id": "ag_1",
        "agent_type": None,
        "link": "linked",
        "turns": [turn(0, "asst", "late work")],
        "parent_tool_use_id": "tu_9",
    }
    orphan = late | {"parent_tool_use_id": "tu_missing", "link": "unlinked"}
    body = content_body()
    body["turns"] = [
        turn(0, "tool", tool=tool("Task", "tu_9", "done")),
        turn(1, "sidechain", sidechain=late),
        turn(2, "sidechain", sidechain=orphan),
    ]

    rendered = _lines(body)

    assert "  sidechain ag_1" in rendered
    assert "2026-10-09T12:00:02Z  sidechain ag_1 · unlinked" in rendered
    assert not any(line.endswith("12:00:01Z  sidechain ag_1") for line in rendered)


def test_an_empty_segment_says_so() -> None:
    body = content_body()
    body["turns"] = []

    assert _lines(body) == ["sg_1  final", "no turns"]


def test_the_index_lists_each_segment_id_first_and_an_empty_one_says_so() -> None:
    listed = list(SegmentIndexLines(index_body("ch_1", "sg_1", "sg_2")).lines())

    assert (
        listed[0]
        == "sg_1  build  epoch 1  generation 0  turns 0-4  final  321 bytes  claude-code  2026-10-09T12:05:00Z"
    )
    assert listed[1].startswith("sg_2  ")
    assert list(SegmentIndexLines(index_body("ch_1")).lines()) == ["no transcript segments"]
