"""The OpenCode 2 transcript source against an in-memory service: forward reads through the service's pages, open
steps read again by id and patched, children as sidechains of their spawning calls, raw records and the tail across
the tree, and a foreign session, a foreign position, or an unreachable service each refused by its own reason."""

from __future__ import annotations

import json
from typing import Any

import structlog

from blizzard.runner.harness.opencode_v2.transcript.normalizer import NORMALIZER_VERSION
from blizzard.runner.harness.opencode_v2.transcript.source import OpenCodeTwoTranscriptSource
from blizzard.runner.harness.transcript import TranscriptBatch, TranscriptErrorFactory, TranscriptPosition
from tests.unit.runner.harness.opencode_v2.service_fake import (
    REGISTRATION,
    TOKENS,
    SessionService,
    SessionTransport,
    assistant,
    idle,
    tool,
)

ROOT = "ses_root"
CHILD = "ses_child"
OURS = {"blizzard": {"runner": "run_1", "lease": "lease_1"}}


def _user(message_id: str, text: str) -> dict[str, Any]:
    return {"type": "user", "id": message_id, "time": {"created": 1}, "text": text}


def _service(*messages: dict[str, Any]) -> SessionService:
    service = SessionService()
    service.add_session(ROOT, metadata=OURS)
    service.messages[ROOT] = list(messages)
    return service


def _source(service: SessionService | None, **options: Any) -> tuple[OpenCodeTwoTranscriptSource, SessionTransport]:
    transport = SessionTransport(service)
    errors = TranscriptErrorFactory(structlog.get_logger("test"))
    return OpenCodeTwoTranscriptSource(lambda: REGISTRATION, transport, errors, runner_id="run_1", **options), transport


def _read(source: OpenCodeTwoTranscriptSource, since: TranscriptPosition | None = None) -> TranscriptBatch:
    return source.turns_since(ROOT, spawn_cwd=None, since=since)


def _subagent(call_id: str, child: str, status: str = "completed") -> dict[str, Any]:
    part = tool(call_id, status, input={"agent": "general", "prompt": "go"}, metadata={"sessionID": child})
    part["name"] = "subagent"
    return part


def test_a_cold_read_normalizes_the_conversation_and_stamps_its_versions() -> None:
    shell = tool("call_1", input={"command": "echo hi"}, content=[{"type": "text", "text": "hi\n"}])
    source, _ = _source(_service(_user("msg_1", "build it"), assistant("msg_2", "done", tools=(shell,))))
    batch = _read(source)
    assert [turn.kind for turn in batch.turns] == ["env", "tool", "asst"]
    call = batch.turns[1].tool
    assert call is not None and (call.name, call.tool_use_id, call.output) == ("shell", "call_1", "hi\n")
    assert dict(call.input) == {"command": "echo hi"}
    assert batch.normalizer_version == NORMALIZER_VERSION and batch.harness_version == "2.1.4"
    assert batch.available and batch.complete and batch.next_position is not None


def test_a_forward_read_returns_only_what_followed_the_position() -> None:
    service = _service(_user("msg_1", "first"), assistant("msg_2", "one"))
    source, _ = _source(service)
    first = _read(source)
    assert _read(source, first.next_position).turns == []
    service.messages[ROOT] += [_user("msg_3", "second"), assistant("msg_4", "two")]
    later = _read(source, first.next_position)
    assert [turn.text for turn in later.turns] == ["second", "two"]


def test_a_long_conversation_is_read_through_the_services_pages_and_never_sends_order_with_a_cursor() -> None:
    service = _service(*(_user(f"msg_{n:04d}", f"line {n}") for n in range(450)))
    source, _ = _source(service)
    assert len(_read(source).turns) == 450
    assert all("order" not in query for _, query in service.listings if "cursor" in query)


def test_a_read_out_of_page_budget_is_incomplete_and_resumes_where_it_stopped() -> None:
    service = _service(*(_user(f"msg_{n:04d}", f"line {n}") for n in range(450)))
    source, _ = _source(service, page_budget=1)
    first = _read(source)
    assert len(first.turns) == 200 and not first.complete
    second = _read(source, first.next_position)
    assert second.turns[0].text == "line 200" and len(second.turns) == 200


def test_an_open_step_ships_its_running_call_then_patches_its_output_and_ships_what_followed() -> None:
    running = assistant("msg_2", tools=(tool("call_1", "running", input={"command": "sleep 40"}),))
    running["time"] = {"created": 2}
    running.pop("tokens")
    running.pop("cost")
    service = _service(_user("msg_1", "sleep"), running)
    source, _ = _source(service)
    first = _read(source)
    call = first.turns[-1].tool
    assert call is not None and call.output is None
    service.messages[ROOT][1] = assistant(
        "msg_2",
        "SLEPT",
        tools=(tool("call_1", input={"command": "sleep 40"}, content=[{"type": "text", "text": "ok"}]),),
    )
    second = _read(source, first.next_position)
    assert [(late.tool_use_id, late.output) for late in second.late_tool_outputs] == [("call_1", "ok")]
    assert [turn.kind for turn in second.turns] == ["asst"]
    assert _read(source, second.next_position).turns == []


def test_text_an_open_step_may_still_be_writing_is_held_until_it_ends() -> None:
    streaming = assistant("msg_2", "half a sent")
    streaming["time"] = {"created": 2}
    service = _service(streaming)
    source, _ = _source(service)
    first = _read(source)
    assert first.turns == []
    service.messages[ROOT][0] = assistant("msg_2", "half a sentence, finished")
    assert [turn.text for turn in _read(source, first.next_position).turns] == ["half a sentence, finished"]


def test_a_child_is_a_sidechain_of_the_call_that_spawned_it() -> None:
    service = _service(_user("msg_1", "delegate"), assistant("msg_2", tools=(_subagent("call_9", CHILD),)))
    service.add_session(CHILD, parent=ROOT)
    service.messages[CHILD] = [_user("msg_c1", "go"), assistant("msg_c2", "CHILD")]
    batch = _read(_source(service)[0])
    sidechain = batch.turns[1].sidechain
    assert sidechain is not None and (sidechain.agent_id, sidechain.agent_type, sidechain.link) == (
        CHILD,
        "general",
        "session-id",
    )
    assert [turn.text for turn in sidechain.turns] == ["go", "CHILD"]
    assert batch.agent_tool_use_ids == {CHILD: "call_9"} and batch.unlinked_sidechains == []


def test_a_child_that_grows_after_its_call_shipped_ships_apart_under_its_session_id() -> None:
    service = _service(assistant("msg_2", tools=(_subagent("call_9", CHILD),)))
    service.add_session(CHILD, parent=ROOT)
    service.messages[CHILD] = [_user("msg_c1", "go")]
    source, _ = _source(service)
    first = _read(source)
    service.messages[CHILD].append(assistant("msg_c2", "more"))
    later = _read(source, first.next_position)
    assert later.turns == []
    assert [(s.agent_id, s.link, [t.text for t in s.turns]) for s in later.unlinked_sidechains] == [
        (CHILD, "unlinked", ["more"])
    ]


def test_a_child_no_call_records_is_an_unlinked_sidechain_of_its_root() -> None:
    service = _service(assistant("msg_2", "alone"))
    service.add_session(CHILD, parent=ROOT)
    service.messages[CHILD] = [_user("msg_c1", "orphan")]
    batch = _read(_source(service)[0])
    assert [(s.agent_id, s.link) for s in batch.unlinked_sidechains] == [(CHILD, "unlinked")]
    assert batch.agent_tool_use_ids == {}


def test_a_compaction_is_a_boundary_carrying_its_summary_and_an_idle_marker_is_no_turn() -> None:
    compaction: dict[str, Any] = {
        "type": "compaction",
        "id": "msg_3",
        "time": {"created": 4},
        "summary": "## Objective",
        "tokens": TOKENS,
        "cost": 0,
    }
    batch = _read(_source(_service(assistant("msg_2", "SEED"), idle("msg_i"), compaction))[0])
    assert [(turn.kind, turn.text) for turn in batch.turns] == [("asst", "SEED"), ("env", "## Objective")]


def test_a_session_another_runner_minted_or_none_holds_is_not_found_everywhere() -> None:
    service = _service(assistant("msg_2", "theirs"))
    service.sessions[ROOT]["metadata"] = {"blizzard": {"runner": "run_other"}}
    source, _ = _source(service)
    assert _read(source).reason == "not_found"
    assert source.turns_since("ses_missing", spawn_cwd=None, since=None).reason == "not_found"
    assert source.read_raw_lines(ROOT, spawn_cwd=None) == []
    assert source.tail_position(ROOT, spawn_cwd=None) is None
    assert source.size_bytes(ROOT, spawn_cwd=None) is None
    assert source.context_tokens(ROOT, spawn_cwd=None) is None


def test_a_position_this_source_did_not_mint_is_unreadable() -> None:
    source, _ = _source(_service(assistant("msg_2", "x")))
    for token in ('{"cursor": {}, "linked_children": []}', "not json", '{"schema": "opencode-v2-transcript/1"}'):
        assert _read(source, TranscriptPosition(token)).reason == "unreadable"


def test_an_unreachable_or_unregistered_service_is_unreadable() -> None:
    assert _read(_source(None)[0]).reason == "unreadable"
    errors = TranscriptErrorFactory(structlog.get_logger("test"))
    unregistered = OpenCodeTwoTranscriptSource(lambda: None, SessionTransport(_service()), errors, runner_id="run_1")
    assert _read(unregistered).reason == "unreadable"


def test_raw_records_cover_the_tree_between_two_positions() -> None:
    service = _service(_user("msg_1", "one"), assistant("msg_2", tools=(_subagent("call_9", CHILD),)))
    service.add_session(CHILD, parent=ROOT)
    service.messages[CHILD] = [assistant("msg_c1", "child")]
    source, _ = _source(service)
    every = [json.loads(line) for line in source.read_raw_lines(ROOT, spawn_cwd=None)]
    assert [(r["session"], r["parent"], r["message"]["id"]) for r in every] == [
        (ROOT, None, "msg_1"),
        (ROOT, None, "msg_2"),
        (CHILD, ROOT, "msg_c1"),
    ]
    start = source.tail_position(ROOT, spawn_cwd=None)
    service.messages[ROOT].append(assistant("msg_3", "two"))
    service.messages[CHILD].append(assistant("msg_c2", "again"))
    end = source.tail_position(ROOT, spawn_cwd=None)
    service.messages[ROOT].append(assistant("msg_4", "after the end"))
    ranged = [
        json.loads(line)["message"]["id"] for line in source.read_raw_lines(ROOT, spawn_cwd=None, start=start, end=end)
    ]
    assert ranged == ["msg_3", "msg_c2"]


def test_a_read_from_the_tail_returns_nothing_until_more_is_written() -> None:
    service = _service(_user("msg_1", "one"), assistant("msg_2", "two"))
    source, _ = _source(service)
    tail = source.tail_position(ROOT, spawn_cwd=None)
    assert _read(source, tail).turns == []
    service.messages[ROOT].append(_user("msg_3", "three"))
    assert [turn.text for turn in _read(source, tail).turns] == ["three"]


def test_size_counts_the_root_alone_and_context_reads_the_newest_root_step() -> None:
    newest = assistant("msg_3", "newer")
    newest["tokens"] = {"input": 7, "output": 2, "reasoning": 0, "cache": {"read": 30, "write": 5}}
    service = _service(assistant("msg_2", "older"), newest, idle("msg_i"))
    service.add_session(CHILD, parent=ROOT)
    service.messages[CHILD] = [assistant("msg_c1", "x" * 10_000)]
    source, _ = _source(service)
    size = source.size_bytes(ROOT, spawn_cwd=None)
    assert size is not None and 0 < size < 10_000
    assert source.context_tokens(ROOT, spawn_cwd=None) == 42


def test_no_read_starts_or_continues_a_turn() -> None:
    service = _service(_user("msg_1", "one"), assistant("msg_2", tools=(_subagent("call_9", CHILD),)))
    service.add_session(CHILD, parent=ROOT)
    source, transport = _source(service)
    first = _read(source)
    _read(source, first.next_position)
    source.read_raw_lines(ROOT, spawn_cwd=None)
    source.tail_position(ROOT, spawn_cwd=None)
    source.size_bytes(ROOT, spawn_cwd=None)
    source.context_tokens(ROOT, spawn_cwd=None)
    assert transport.requests and {request.method for request in transport.requests} == {"GET"}
