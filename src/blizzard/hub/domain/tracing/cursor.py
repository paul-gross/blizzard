"""Trace export cursor key (``blizzard-product:/delivered/tracing/fleet-spans/spec/emission.md`` §The cursor)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from blizzard.hub.domain.tracing.steps import NodeStep

_CHUNK_EPOCH = -1
_COMPLETED = "completed"
_ONE_MICROSECOND = timedelta(microseconds=1)


@dataclass(frozen=True, order=True)
class CursorKey:
    """A position in the total order of closed steps and finished chunks: time, chunk, epoch, decision.

    ``decision_id`` is empty for runner and hub steps; a chunk's own items sit at epoch ``-1``, ahead of the
    instant's steps. :meth:`opening` and :meth:`past` bound every item at an instant."""

    at: datetime
    chunk_id: str = ""
    epoch: int = 0
    decision_id: str = ""

    @classmethod
    def of(cls, step: NodeStep) -> CursorKey:
        if step.close is None:
            raise ValueError(f"step {step.key.text()} is open and has no cursor position")
        return cls(step.close.at, step.key.chunk_id, step.epoch, step.decision_id or "")

    @classmethod
    def chunk_finished(cls, at: datetime, chunk_id: str) -> CursorKey:
        return cls(at, chunk_id, _CHUNK_EPOCH)

    @classmethod
    def chunk_completed(cls, at: datetime, chunk_id: str) -> CursorKey:
        return cls(at, chunk_id, _CHUNK_EPOCH, _COMPLETED)

    @classmethod
    def opening(cls, at: datetime) -> CursorKey:
        return cls(at)

    @classmethod
    def past(cls, at: datetime) -> CursorKey:
        return cls(at + _ONE_MICROSECOND)
