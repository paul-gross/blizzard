"""The transcript pump's per-window decisions — pure, over a loaded segment and plain counts.

The pump reads the source, resolves the harness, and carries a plan out; these decide what
that plan is: whether a segment is read at all this window, what a window's read turns into,
and whether a drain keeps reading."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from blizzard.foundation.roles import domain_model
from blizzard.runner.transcripts.ledger import CHUNK_BUDGET_EXCEEDED, TranscriptSegmentState

__all__ = [
    "PreRead",
    "PumpOutcome",
    "WindowPlan",
    "after_window",
    "plan_window",
    "pre_read",
]

#: One window's outcome; ``not_attempted`` and ``stuck`` count as incomplete: a finalizing segment gets no later window.
PumpOutcome = Literal["caught_up", "incomplete", "not_attempted", "stuck"]


@domain_model
@dataclass(frozen=True)
class PreRead:
    """Whether a segment is read this window. ``read``: go ahead. ``stop``: stop shipping it
    for ``stop_reason`` and count it caught up. ``skip``: do nothing, with ``outcome``."""

    action: Literal["read", "stop", "skip"]
    outcome: PumpOutcome | None = None
    stop_reason: str | None = None


def pre_read(
    segment: TranscriptSegmentState,
    *,
    chunk_shipped_bytes: int,
    chunk_max_bytes: int,
    outstanding_bytes: int,
    max_buffered_bytes: int,
) -> PreRead:
    """The checks before any source read, in order: a stopped segment ships nothing (caught
    up); a finalized one takes no content (not attempted); a spent chunk budget stops it for
    good (caught up); a full outbound buffer is transient backpressure (not attempted)."""
    if segment.shipping_stopped:
        return PreRead(action="skip", outcome="caught_up")
    if not segment.accepts_content:
        return PreRead(action="skip", outcome="not_attempted")
    if chunk_shipped_bytes >= chunk_max_bytes:
        return PreRead(action="stop", outcome="caught_up", stop_reason=CHUNK_BUDGET_EXCEEDED)
    if outstanding_bytes >= max_buffered_bytes:
        return PreRead(action="skip", outcome="not_attempted")
    return PreRead(action="read")


@domain_model
@dataclass(frozen=True)
class WindowPlan:
    """What one read window turns into. ``advance_cursor``: no content, but the source moved
    — remember the new cursor. ``nothing``: no content and no movement. ``stuck``: content,
    yet the cursor did not move. ``stop``: the content would overrun the chunk budget —
    stop shipping, ship none of it. ``ship``: record the deltas."""

    action: Literal["advance_cursor", "nothing", "stuck", "stop", "ship"]
    outcome: PumpOutcome
    stop_reason: str | None = None


def plan_window(
    *,
    cursor: str | None,
    new_cursor: str | None,
    has_content: bool,
    total_bytes: int,
    chunk_shipped_bytes: int,
    chunk_max_bytes: int,
    complete: bool,
) -> WindowPlan:
    """Decide a read window. Content must move the cursor or the same turns re-ship every
    tick; and it ships all-or-nothing against the chunk budget, since every record of one
    window advances the same cursor write."""
    read_outcome: PumpOutcome = "caught_up" if complete else "incomplete"
    if not has_content:
        if new_cursor is not None and new_cursor != cursor:
            return WindowPlan(action="advance_cursor", outcome=read_outcome)
        return WindowPlan(action="nothing", outcome=read_outcome)
    if new_cursor is None or new_cursor == cursor:
        return WindowPlan(action="stuck", outcome="stuck")
    if chunk_shipped_bytes + total_bytes > chunk_max_bytes:
        return WindowPlan(action="stop", outcome="caught_up", stop_reason=CHUNK_BUDGET_EXCEEDED)
    return WindowPlan(action="ship", outcome=read_outcome)


def after_window(outcome: PumpOutcome, *, deadline_passed: bool) -> Literal["read_to_end", "continue", "incomplete"]:
    """Whether a drain keeps reading after one window. ``read_to_end``: caught up. ``incomplete``:
    retrying gains nothing (not attempted, stuck) or the deadline passed — the caller marks the
    loss. A segment a closing lease never reached is ``not_attempted`` here too."""
    if outcome == "caught_up":
        return "read_to_end"
    if outcome in ("not_attempted", "stuck") or deadline_passed:
        return "incomplete"
    return "continue"
