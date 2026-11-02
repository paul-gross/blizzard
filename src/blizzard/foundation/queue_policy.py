"""The matched fleet peek's hold-or-pass-over policy."""

from __future__ import annotations

from enum import StrEnum


class QueueMatchPolicy(StrEnum):
    """The matched fleet peek's hold-or-pass-over policy — applied to the capability-eligibility and
    blocked-dependency dimensions together, never one alone. The hub reads any other wire value as ``PASS_OVER``."""

    HOLD = "hold"
    PASS_OVER = "pass-over"
