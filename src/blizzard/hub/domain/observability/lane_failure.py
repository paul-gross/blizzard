"""Whether an export lane's newest failure still stands — shared by the trace export and fact egress."""

from __future__ import annotations


def failure_ongoing(failure: object | None, newest_latch: str | None, *, failed_kind: str) -> bool:
    """A failure stands while one was recorded and the lane's newest latch fact is still its failure
    kind; a later recovery fact ends it."""
    return failure is not None and newest_latch == failed_kind
