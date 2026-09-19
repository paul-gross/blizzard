"""``TranscriptPump`` driven against the real ``OpenCodeTranscriptSource`` (review F3, blocking)
— component tier. The unit-tier tests in ``test_runner_harness_opencode_transcript.py`` prove
the source itself ships a ``LateToolOutput`` patch, not a duplicate turn, on a pending tool
call's completion; this file proves the SAME scenario end to end through the pump, mirroring
``test_transcript_pump.py::test_a_result_whose_call_shipped_last_window_rides_as_an_output_patch``
for Claude Code's own late-output case."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.runner.domain.leases import NewLease
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import OPENCODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.internal.opencode_export import OpenCodeExportError
from blizzard.runner.harness.internal.opencode_transcript_source import OpenCodeTranscriptSource
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.transcript_pump import TranscriptPump
from tests.runner_fakes import FakeHarness, FakeHub, FakeProbe, FakeProvider, make_context, make_store
from tests.test_runner_harness_opencode_transcript import (
    FakeExporter,
    _assistant_message,
    _error_factory,
    _export,
    _tool_part,
    _user_message,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _registry(harness: FakeHarness, source: OpenCodeTranscriptSource) -> HarnessRegistry:
    return HarnessRegistry({OPENCODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=source)})


def _open_segment(ctx) -> str:  # type: ignore[no-untyped-def]
    ctx.stores.environments.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    ctx.stores.lease_record.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    ctx.stores.liveness.record_spawn(
        "lease_1",
        pid=1,
        process_start_time="1",
        session=SessionReference(OPENCODE_HARNESS_ID, "sess-1"),
        spawned_at=_NOW,
    )
    return ctx.stores.transcript_ledger.open_transcript_segments()[0].segment_id


def _shipped_turns(ctx) -> list[dict]:  # type: ignore[no-untyped-def]
    return [
        turn
        for delta in ctx.stores.transcript_ledger.pending_transcript_outbound(limit=50)
        if not delta.final
        for turn in json.loads(delta.payload)["turns"]
    ]


def test_a_pending_tool_call_completing_on_a_later_window_never_ships_twice() -> None:
    """review F3 end to end: a real ``OpenCodeTranscriptSource`` reading a pending tool call,
    then its completion on a later tick, ships exactly ONE full tool turn (the pending one)
    plus one output-patch turn — never the completed call a second time."""
    exporter = FakeExporter(
        {
            "sess-1": _export(
                "sess-1",
                [
                    _user_message("sess-1", "m-user", []),
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
    store = make_store("sqlite://")
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-1", pid=1, process_start_time="1", pgid=1),
        verdict=None,
        transcript_source=source,
    )
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", transcripts_ship=True),
    )
    ctx = replace(ctx, harnesses=_registry(harness, source))
    _open_segment(ctx)

    TranscriptPump(ctx).run()

    shipped_first = _shipped_turns(ctx)
    [pending_turn] = [t for t in shipped_first if t["kind"] == "tool"]
    assert pending_turn["tool"]["tool_use_id"] == "call-1"
    assert pending_turn["tool"]["output"] is None
    assert pending_turn["tool"].get("output_patch") is not True

    exporter.scripts["sess-1"] = _export(
        "sess-1",
        [
            _user_message("sess-1", "m-user", []),
            _assistant_message(
                "sess-1",
                "m-asst",
                [_tool_part("sess-1", "m-asst", "p-tool", call_id="call-1", status="completed", output="done")],
            ),
        ],
    )

    TranscriptPump(ctx).run()

    every_shipped_turn = _shipped_turns(ctx)
    tool_turns = [t for t in every_shipped_turn if t["kind"] == "tool"]
    # Exactly two turns ever shipped for this one call: the original pending turn, and its
    # output patch — never a second, full "completed" turn duplicating the first.
    assert len(tool_turns) == 2
    full_calls = [t for t in tool_turns if t["tool"].get("output_patch") is not True]
    patches = [t for t in tool_turns if t["tool"].get("output_patch") is True]
    assert len(full_calls) == 1
    [patch] = patches
    assert patch["tool"]["tool_use_id"] == "call-1"
    assert patch["tool"]["output"] == "done"

    # A third, unchanged tick ships nothing further at all.
    TranscriptPump(ctx).run()
    assert len(_shipped_turns(ctx)) == len(every_shipped_turn)


def test_an_unresolved_child_sidechain_is_visible_and_picked_up_on_a_later_window() -> None:
    """review F4 end to end: a child session export that fails once, then succeeds, surfaces
    as an unlinked sidechain (never lost) and is picked up through the pump's own
    cross-window agent-id route once it resolves — never a duplicate on a further tick."""
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
                        output="done",
                        metadata={"sessionID": "child-1"},
                    )
                ],
            )
        ],
    )
    exporter = FakeExporter({"root-1": root, "child-1": OpenCodeExportError("gone")})
    source = OpenCodeTranscriptSource(exporter, _error_factory())
    store = make_store("sqlite://")
    harness = FakeHarness(
        handle=WorkerHandle(session_id="root-1", pid=1, process_start_time="1", pgid=1),
        verdict=None,
        transcript_source=source,
    )
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", transcripts_ship=True),
    )
    ctx = replace(ctx, harnesses=_registry(harness, source))
    ctx.stores.environments.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    ctx.stores.lease_record.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    ctx.stores.liveness.record_spawn(
        "lease_1",
        pid=1,
        process_start_time="1",
        session=SessionReference(OPENCODE_HARNESS_ID, "root-1"),
        spawned_at=_NOW,
    )

    TranscriptPump(ctx).run()

    # Nothing ships to the hub yet — no parent is confirmed — but it stays a live retry, not
    # a dropped one: exactly one WARNING event, no more, per the pump's own latch.
    kinds = [json.loads(e.payload)["kind"] for e in ctx.stores.outbound.pending_outbound()]
    assert kinds.count("transcript-sidechain-dropped") == 1
    assert [t for t in _shipped_turns(ctx) if t["kind"] == "sidechain"] == []

    exporter.scripts["child-1"] = _export("child-1", [_user_message("child-1", "m-child-user", [])], parent_id="root-1")

    TranscriptPump(ctx).run()

    [linked] = [t for t in _shipped_turns(ctx) if t["kind"] == "sidechain"]
    assert linked["sidechain"]["agent_id"] == "child-1"
    assert linked["sidechain"]["parent_tool_use_id"] == "call-task"

    # A further, unchanged tick never re-ships the same conversation again.
    shipped_before = _shipped_turns(ctx)
    TranscriptPump(ctx).run()
    assert _shipped_turns(ctx) == shipped_before
