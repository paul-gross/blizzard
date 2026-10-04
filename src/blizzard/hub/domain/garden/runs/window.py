"""The time-window refusal the garden's run reads share — a span must run forward."""

from __future__ import annotations

from datetime import datetime


class InvalidWindowError(ValueError):
    """A requested ``[since, until)`` window is empty, inverted, or too wide — the
    message names which."""


def require_until_after_since(since: datetime, until: datetime) -> None:
    """``until`` strictly after ``since``; an empty or inverted span refuses."""
    if until <= since:
        raise InvalidWindowError("until must be after since")
