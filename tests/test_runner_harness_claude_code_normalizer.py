"""``harness/internal/claude_code_normalizer.py`` (blizzard#245).

Unit tier: :func:`normalize_lines` takes an iterable of strings and needs no
filesystem — thinking turns, structured tool input, sidechain assembly and its
record-level link routes, version stamps, and the widened control skip list."""

from __future__ import annotations

import json

import pytest

from blizzard.runner.harness.internal import claude_code_normalizer as normalizer_module
from blizzard.runner.harness.internal.claude_code_normalizer import Record, Run, normalize_lines
from tests import transcript_fixtures as fx

# --- Record collapse — env/asst/tool ---


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
        # Already inert (matches neither the assistant nor user branch) — drop made explicit.
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


# --- New: thinking turns ---


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


# --- New: structured tool input (never json.dumps'd) ---


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
def test_list_valued_tool_input_is_shaped_other_with_its_json_form_preserved() -> None:
    """The fourth shape (`"other"`): a non-object, non-string, non-null `input` is
    never coerced; its `json.dumps` form is held verbatim on `input_unparsed`."""
    content = [{"type": "tool_use", "id": "t1", "name": "Weird", "input": [1, "two", True]}]
    line = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": content}, "uuid": "a1"})
    result = normalize_lines([line])

    tool = result.turns[0].tool
    assert tool is not None
    assert tool.input == {}
    assert tool.input_unparsed == json.dumps([1, "two", True])
    assert tool.input_shape == "other"


@pytest.mark.unit
def test_absent_tool_input_is_shaped_absent_not_object() -> None:
    """Distinct from an actual empty object (``input_shape == "object"``) — the
    re-materializing projection treats the two differently."""
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


# --- New: version stamps ---


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


# --- New: sidechain link routes (blizzard#245) ---


@pytest.mark.unit
def test_agent_id_join_candidate_surfaces_for_a_tool_result_carrying_agent_id() -> None:
    """Route 1 (agent-id) is resolved by the sibling source module, not here — this
    normalizer only surfaces the candidate turn index, keyed by the agentId its
    `tool_result` carried — the exact join key the source's sidecar-file lookup uses."""
    lines = [
        fx.assistant_tool_use("t1", "Task", {"subagent_type": "explorer", "prompt": "find X"}),
        fx.tool_result("t1", "subagent finished", agent_id="agent-abc"),
    ]
    result = normalize_lines(lines)

    assert len(result.turns) == 1
    assert result.agent_id_by_tool_turn == {0: "agent-abc"}
    assert result.discovered_agent_ids == frozenset({"agent-abc"})
    assert result.turns[0].sidechain is None  # not resolved here — the source's job


@pytest.mark.unit
def test_agent_id_is_not_attributed_when_one_record_resolves_two_tool_results() -> None:
    """An ambiguous record with more than one `tool_result` is not stamped as
    *attached* (F13's guard), but must still surface as a discovered candidate."""
    lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "job 1"}, uuid="a1"),
        fx.assistant_tool_use("t2", "Task", {"prompt": "job 2"}, uuid="a2"),
        json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "done 1"},
                        {"type": "tool_result", "tool_use_id": "t2", "content": "done 2"},
                    ],
                },
                "toolUseResult": {"agentId": "agent-ambiguous"},
                "timestamp": "2026-07-16T10:00:03Z",
                "uuid": "u2",
            }
        ),
    ]
    result = normalize_lines(lines)

    assert result.agent_id_by_tool_turn == {}
    assert result.discovered_agent_ids == frozenset({"agent-ambiguous"})
    tool_turns = [t for t in result.turns if t.kind == "tool"]
    assert len(tool_turns) == 2
    assert tool_turns[0].tool is not None
    assert tool_turns[1].tool is not None
    assert tool_turns[0].tool.output == "done 1"
    assert tool_turns[1].tool.output == "done 2"


@pytest.mark.unit
def test_agent_id_is_a_discovered_candidate_even_when_its_tool_use_is_not_in_these_lines() -> None:
    """F1: `discovered_agent_ids` must not share `agent_id_by_tool_turn`'s limitation
    of needing a matching `tool_use` in the same `normalize_lines` call."""
    lines = [fx.tool_result("t1", "spawned", agent_id="agent-abc")]  # no matching tool_use in these lines
    result = normalize_lines(lines)

    assert result.agent_id_by_tool_turn == {}
    assert result.discovered_agent_ids == frozenset({"agent-abc"})


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
def test_prompt_timestamp_route_skips_an_already_claimed_candidate() -> None:
    """Two sidechain runs matching the same prompt-timestamp key with only one
    eligible tool call: the second falls through to `unlinked`, never overwriting
    the first's claim."""
    lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "same prompt"}, uuid="spawn-1", ts="2026-07-16T09:59:00Z"),
        fx.sidechain_run_record(
            "same prompt", uuid="run1-root", parent_uuid="orphan1", role="user", ts="2026-07-16T10:00:00Z"
        ),
        fx.sidechain_run_record("run1 reply", uuid="run1-reply", parent_uuid="run1-root", ts="2026-07-16T10:00:01Z"),
        fx.sidechain_run_record(
            "same prompt", uuid="run2-root", parent_uuid="orphan2", role="user", ts="2026-07-16T10:00:00Z"
        ),
        fx.sidechain_run_record("run2 reply", uuid="run2-reply", parent_uuid="run2-root", ts="2026-07-16T10:00:01Z"),
    ]
    result = normalize_lines(lines)

    claimed_tool_turns = [t for t in result.turns if t.kind == "tool" and t.sidechain is not None]
    assert len(claimed_tool_turns) == 1
    sidechain = claimed_tool_turns[0].sidechain
    assert sidechain is not None
    assert sidechain.link == "prompt-timestamp"
    assert len(result.unlinked_sidechains) == 1
    assert result.unlinked_sidechains[0].link == "unlinked"


@pytest.mark.unit
def test_prompt_timestamp_route_treats_a_candidate_with_no_timestamp_as_a_non_match() -> None:
    """A tool turn indexed by prompt but carrying no timestamp of its own can never
    win "nearest preceding" — it resolves to no spawn rather than an unevaluated
    "first match wins"."""
    lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "untimed prompt"}, uuid="spawn-untimed"),
        fx.sidechain_run_record(
            "untimed prompt", uuid="run-root", parent_uuid="orphan", role="user", ts="2026-07-16T10:00:00Z"
        ),
    ]
    # Strip the timestamp the fixture always stamps, since it must be absent here.
    record = json.loads(lines[0])
    del record["timestamp"]
    lines[0] = json.dumps(record)

    result = normalize_lines(lines)

    assert len(result.unlinked_sidechains) == 1
    assert result.unlinked_sidechains[0].link == "unlinked"


@pytest.mark.unit
def test_prompt_timestamp_route_tolerates_an_offset_less_candidate_timestamp() -> None:
    """`Record.at` coerces an offset-less stamp to UTC rather than leaving it naive,
    so comparing an offset-less and an aware timestamp never raises `TypeError`."""
    lines = [
        fx.assistant_tool_use("t1", "Task", {"prompt": "find X"}, uuid="spawn-1", ts="2026-07-16T09:59:00Z"),
        fx.sidechain_run_record("find X", uuid="run-root", parent_uuid="orphan", role="user", ts="2026-07-16T10:00:00"),
    ]

    result = normalize_lines(lines)

    claimed_tool_turns = [t for t in result.turns if t.kind == "tool" and t.sidechain is not None]
    assert len(claimed_tool_turns) == 1
    sidechain = claimed_tool_turns[0].sidechain
    assert sidechain is not None
    assert sidechain.link == "prompt-timestamp"


@pytest.mark.unit
def test_unresolvable_inline_sidechain_surfaces_unlinked_not_among_top_level_turns() -> None:
    """An isSidechain record with no route to resolve its parent yields zero
    top-level turns, but surfaces as data on `unlinked_sidechains` rather than being
    dropped silently."""
    result = normalize_lines([fx.sidechain_record()])

    assert result.turns == []
    assert len(result.unlinked_sidechains) == 1
    assert result.unlinked_sidechains[0].link == "unlinked"


@pytest.mark.unit
def test_threading_stays_fast_under_duplicate_uuid_values() -> None:
    """A duplicate `uuid` value shared by every record on a chain — not a shared
    `parentUuid` alone — is what degrades a naive per-link rescan to quadratic."""
    import time

    # 40,000 records — the quadratic implementation measured 29.4s here, so the 5s
    # threshold carries ~6x headroom against a reintroduced quadratic walk.
    n = 40_000
    records: list[dict[str, object]] = [
        {"type": "user", "uuid": "dup", "parentUuid": "orphan", "isSidechain": True},
    ]
    for i in range(n):
        records.append(
            {
                "type": "assistant",
                "uuid": "dup",  # every link shares the same uuid as its predecessor
                "parentUuid": "dup",  # ...and the same parentUuid, so all land in one bucket
                "isSidechain": True,
                "message": {"role": "assistant", "content": f"link {i}"},
            }
        )

    start = time.monotonic()
    runs = Run.thread([Record(r) for r in records])
    elapsed = time.monotonic() - start

    assert elapsed < 5.0, (
        f"threading {len(records)} duplicate-uuid records took {elapsed:.1f}s — likely quadratic again"
    )
    assert len(runs) == 1
    assert len(runs[0].records) == n + 1
    assert [r.content for r in runs[0].records[1:]] == [f"link {i}" for i in range(n)]


# --- New: sidecar-file normalization (is_sidechain_file=True) ---


@pytest.mark.unit
def test_sidecar_file_records_normalize_as_their_own_main_conversation() -> None:
    """Every sidecar record carries `isSidechain: true`; `is_sidechain_file=True`
    must treat that as the file's own conversation, not re-route it into a further
    sidechain bucket."""
    lines = [
        fx.sidecar_record("subagent starting", role="user", agent_id="agent-abc"),
        fx.sidecar_record("subagent done", role="assistant", agent_id="agent-abc"),
    ]
    result = normalize_lines(lines, is_sidechain_file=True)

    assert [t.kind for t in result.turns] == ["env", "asst"]
    assert result.unlinked_sidechains == []
