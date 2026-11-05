"""The ``opencode-v2/1`` normalizer over every admitted version's captured messages: each kind maps to its turn, a
call keeps its structured input and output, a step still writing ships only through its last call, and a ``subagent``
call names the child it spawned."""

from __future__ import annotations

import json

import pytest

from blizzard.runner.harness.admission import version_admitted
from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.json_value import JsonValue
from blizzard.runner.harness.opencode_v2.shapes import Message, parse_message, parse_part
from blizzard.runner.harness.opencode_v2.transcript.normalizer import (
    is_open,
    late_outputs,
    message_turns,
    shippable,
    spawned_children,
)
from blizzard.runner.harness.opencode_v2.version import ADMITTED_OPENCODE_V2_RANGE

_CORPUS = CommittedCorpus()
_VERSIONS = sorted(v for v in _CORPUS.versions("opencode") if version_admitted(v, ADMITTED_OPENCODE_V2_RANGE))


def _fixture(version: str, name: str) -> JsonValue:
    return json.loads((_CORPUS.root / "opencode" / version / f"{name}.json").read_text())


def _messages(version: str, name: str) -> list[Message]:
    loaded = _fixture(version, name)
    assert isinstance(loaded, list)
    return [parse_message(item) for item in loaded]


@pytest.mark.parametrize("version", _VERSIONS)
def test_the_read_and_skill_calls_keep_their_input_and_output(version: str) -> None:
    turns = [
        turn for message in _messages(version, "transcript_tools_messages") for turn in message_turns(message).turns
    ]
    calls = {turn.tool.name: turn.tool for turn in turns if turn.tool is not None}
    assert set(calls) >= {"read", "skill"}
    assert dict(calls["read"].input) == {"path": "NOTES.md"} and "first line" in (calls["read"].output or "")
    assert dict(calls["skill"].input) == {"id": "proof-skill"} and calls["skill"].tool_use_id
    assert turns[0].kind == "env" and turns[-1].kind == "asst"


@pytest.mark.parametrize("version", _VERSIONS)
def test_a_step_still_running_ships_its_call_with_no_output_and_patches_it_once_read_again(version: str) -> None:
    live = next(m for m in _messages(version, "transcript_live") if m.type == "assistant")
    assert is_open(live) and shippable(live) == len(live.parts)
    made = message_turns(live)
    assert [turn.kind for turn in made.turns] == ["tool"] and made.pending
    after = next(m for m in _messages(version, "transcript_after") if m.id == live.id)
    assert not is_open(after)
    patches, waiting = late_outputs(after, made.pending)
    assert [patch.tool_use_id for patch in patches] == list(made.pending) and waiting == ()
    assert "SLEPT" in patches[0].output


@pytest.mark.parametrize("version", _VERSIONS)
def test_a_subagent_call_names_the_child_it_spawned(version: str) -> None:
    parts = _fixture(version, "child_spawn_parts")
    assert isinstance(parts, list)
    message = Message(type="assistant", id="msg_spawn", created=1, completed=2, parts=tuple(map(parse_part, parts)))
    children = spawned_children(message)
    assert len(children) == 1
    ((child, call),) = children.items()
    assert child.startswith("ses_") and call.startswith("call_")


@pytest.mark.parametrize("version", _VERSIONS)
def test_a_compaction_carries_its_summary_and_markers_make_no_turn(version: str) -> None:
    messages = _messages(version, "compaction_messages")
    by_type = {message.type: message_turns(message).turns for message in messages}
    assert [turn.kind for turn in by_type["compaction"]] == ["env"] and by_type["compaction"][0].text
    assert by_type["idle"] == []


@pytest.mark.parametrize("version", _VERSIONS)
def test_a_failed_call_carries_its_error_as_its_output(version: str) -> None:
    turns = [turn for message in _messages(version, "interrupt_root_messages") for turn in message_turns(message).turns]
    failed = [turn.tool for turn in turns if turn.tool is not None and turn.tool.output == "Tool execution interrupted"]
    assert failed


def test_text_after_an_open_steps_last_call_is_held() -> None:
    raw: JsonValue = {
        "type": "assistant",
        "id": "msg_1",
        "time": {"created": 1},
        "content": [
            {"type": "reasoning", "text": "think"},
            {"type": "tool", "id": "call_1", "name": "shell", "state": {"status": "completed", "content": []}},
            {"type": "text", "text": "still writ"},
        ],
    }
    message = parse_message(raw)
    assert shippable(message) == 2
    assert [turn.kind for turn in message_turns(message).turns] == ["thinking", "tool"]
    assert [turn.text for turn in message_turns(message, 2).turns] == []
