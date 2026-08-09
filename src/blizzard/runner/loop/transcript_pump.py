"""The transcript lane's per-tick pump (issue #246) — advances each live segment's
forward-read cursor through ``IHarnessTranscriptSource.turns_since`` and enqueues its
delta atomically with the cursor write (D3). A no-op while ``ctx.config.transcripts_ship``
is ``False`` (D5): the whole lane costs nothing when shipping is off."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.transcript import NormalizedTurn, SidechainConversation, ToolCall, TranscriptPosition
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.outbound import OutboundFacts
from blizzard.runner.store.repository import TranscriptSegmentRecord
from blizzard.wire.transcript_outbound import TRANSCRIPT_RECORD_MAX_BYTES

#: The per-chunk budget (D4) — measured as the sum of `shipped_bytes` across the chunk's
#: segments, the only quantity the runner controls and the hub bills against.
CHUNK_TRANSCRIPT_MAX_BYTES = 64 * 1024 * 1024

#: Reject-but-truncate reasons (D4) — recorded on the segment, never silent.
_RECORD_CAP_EXCEEDED = "record_cap_exceeded"
_CHUNK_BUDGET_EXCEEDED = "chunk_budget_exceeded"


@dataclass(frozen=True)
class TranscriptPump:
    """Advances every live segment one tick's worth forward — the lane's only producer
    of transcript deltas. Not yet a tick :class:`~blizzard.runner.loop.steps.Step` itself;
    a later phase wires it (and the drain) into a registered step (D3)."""

    ctx: LoopContext

    def run(self) -> None:
        if not self.ctx.config.transcripts_ship or self.ctx.transcripts is None:
            return
        for segment in self.ctx.store.open_transcript_segments():
            self._pump_one(segment)

    def _pump_one(self, segment: TranscriptSegmentRecord) -> None:
        if segment.truncated_reason is not None:
            return  # already stopped shipping this chunk's content (D4)
        budget_before = self.ctx.store.chunk_transcript_shipped_bytes(segment.chunk_id)
        if budget_before >= CHUNK_TRANSCRIPT_MAX_BYTES:
            self._truncate(segment, _CHUNK_BUDGET_EXCEEDED)
            return

        bindings = self.ctx.store.bindings_for_chunk(segment.chunk_id)
        spawn_cwd = SpawnCwd(self.ctx.config.workspace_root, bindings[0].workdir if bindings else None).path
        source = self.ctx.transcripts
        assert source is not None  # guarded in run()
        since = TranscriptPosition(segment.cursor) if segment.cursor is not None else None
        batch = source.turns_since(segment.session_id, spawn_cwd=spawn_cwd, since=since)
        if not batch.available or not batch.turns:
            return  # nothing new this tick

        delta: dict[str, Any] = {"segment_id": segment.segment_id, "turns": [_turn_wire(t) for t in batch.turns]}
        payload = json.dumps(delta)
        record_truncated = False
        if len(payload.encode("utf-8")) > TRANSCRIPT_RECORD_MAX_BYTES:
            delta = _shrink_to_cap(delta)
            payload = json.dumps(delta)
            record_truncated = True

        delta_bytes = len(payload.encode("utf-8"))
        if budget_before + delta_bytes > CHUNK_TRANSCRIPT_MAX_BYTES:
            self._truncate(segment, _CHUNK_BUDGET_EXCEEDED)
            return

        new_cursor = batch.next_position.token if batch.next_position is not None else segment.cursor
        self.ctx.store.record_transcript_delta(
            segment_id=segment.segment_id,
            chunk_id=segment.chunk_id,
            cursor=new_cursor,
            shipped_bytes=segment.shipped_bytes + delta_bytes,
            shipped_turns=segment.shipped_turns + len(batch.turns),
            payload=payload,
            created_at=self.ctx.clock.now(),
        )
        if record_truncated:
            self._truncate(segment, _RECORD_CAP_EXCEEDED)

    def _truncate(self, segment: TranscriptSegmentRecord, reason: str) -> None:
        self.ctx.store.truncate_transcript_segment(segment.segment_id, reason=reason)
        OutboundFacts(self.ctx).transcript_truncated(
            chunk_id=segment.chunk_id, segment_id=segment.segment_id, reason=reason, at=self.ctx.clock.now()
        )


def _turn_wire(turn: NormalizedTurn) -> dict[str, Any]:
    return {
        "index": turn.index,
        "kind": turn.kind,
        "timestamp": iso_utc(turn.timestamp) if turn.timestamp is not None else None,
        "text": turn.text,
        "tool": _tool_wire(turn.tool) if turn.tool is not None else None,
        "thinking_redacted": turn.thinking_redacted,
        "sidechain": _sidechain_wire(turn.sidechain) if turn.sidechain is not None else None,
        "truncated": turn.truncated,
    }


def _tool_wire(tool: ToolCall) -> dict[str, Any]:
    return {
        "name": tool.name,
        "input": tool.input,
        "input_unparsed": tool.input_unparsed,
        "input_shape": tool.input_shape,
        "tool_use_id": tool.tool_use_id,
        "output": tool.output,
        "output_truncated": tool.output_truncated,
    }


def _sidechain_wire(sidechain: SidechainConversation) -> dict[str, Any]:
    return {
        "agent_id": sidechain.agent_id,
        "agent_type": sidechain.agent_type,
        "link": sidechain.link,
        "turns": [_turn_wire(t) for t in sidechain.turns],
    }


def _shrink_to_cap(delta: dict[str, Any]) -> dict[str, Any]:
    """Shrink turn text in place until the serialized delta fits the per-record cap (D4).

    Never drops a turn, so the cursor still advances past the whole batch: the largest
    ``text`` field is halved, repeatedly, until the encoding fits or nothing is left."""
    turns = delta["turns"]
    for _ in range(200):
        if len(json.dumps(delta).encode("utf-8")) <= TRANSCRIPT_RECORD_MAX_BYTES:
            break
        candidates = [t for t in turns if t.get("text")]
        if not candidates:
            break  # nothing left to shrink; the cap stays exceeded by structure alone
        target = max(candidates, key=lambda t: len(t["text"]))
        text = target["text"]
        target["text"] = text[: len(text) // 2]
        target["truncated"] = True
    return delta
