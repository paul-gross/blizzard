"""The finding vocabularies the hub's fold and the wire share — one definition each.

What each fact kind and state means is blizzard-context:/domain/findings-and-proposals.md's own."""

from __future__ import annotations

from enum import StrEnum


class FindingFactKind(StrEnum):
    """One append-only transformation a finding's fact chain records."""

    ADD = "add"
    OBSERVED = "observed"
    GONE = "gone"
    DELIVERED = "delivered"
    RESOLVED = "resolved"
    GONE_CONFIRMED = "gone-confirmed"
    WONT_FIX = "wont-fix"
    NOT_A_FINDING = "not-a-finding"
    SUPERSEDED = "superseded"
    REOPENED = "reopened"


class FindingState(StrEnum):
    """A finding's derived liveness — ``live`` for a live-making newest fact, else the newest
    fact's own kind."""

    LIVE = "live"
    GONE = "gone"
    DELIVERED = "delivered"
    RESOLVED = "resolved"
    GONE_CONFIRMED = "gone-confirmed"
    WONT_FIX = "wont-fix"
    NOT_A_FINDING = "not-a-finding"
    SUPERSEDED = "superseded"


class FindingExit(StrEnum):
    """How an exited finding left: ``outflow`` when the ground itself changed, ``withdrawn``
    when a person judged the finding rather than the code."""

    OUTFLOW = "outflow"
    WITHDRAWN = "withdrawn"


class FindingSeverity(StrEnum):
    """A review-sourced finding's own severity."""

    BLOCKING = "blocking"
    SHOULD_FIX = "should-fix"


class FindingSource(StrEnum):
    """A finding's home — the routine run or the chunk review that raised it."""

    ROUTINE = "routine"
    REVIEW = "review"
