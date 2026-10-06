"""The ``--since``/``--until`` window flags CLI verbs share — one declaration of each
flag, a bare time read in the caller's local time and a zoned one as its own instant, converted to UTC for the wire."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any, overload

import click

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.store.utc import iso_utc

_ZONED = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")


class WindowTime(click.DateTime):
    """The click ``datetime`` type, which also takes an RFC 3339 instant with ``Z`` or a ``±hh:mm`` offset, as an
    export writes its times. A zoned value becomes the naive local wall clock of that instant, so every consumer
    reads it as it reads a bare one."""

    def convert(self, value: Any, param: click.Parameter | None, ctx: click.Context | None) -> Any:
        if isinstance(value, str) and _ZONED.fullmatch(value):
            try:
                return datetime.fromisoformat(value).astimezone().replace(tzinfo=None)
            except ValueError:
                self.fail(f"{value!r} is not a valid RFC 3339 time.", param, ctx)
        return super().convert(value, param, ctx)


@overload
def utc_query_value(value: datetime) -> str: ...
@overload
def utc_query_value(value: None) -> None: ...
def utc_query_value(value: datetime | None) -> str | None:
    """A bare ``--since``/``--until`` is read as the caller's own local wall clock, not
    UTC — converted (not merely relabeled) before it crosses the wire."""
    return iso_utc(value.astimezone(UTC)) if value is not None else None


def since_option(*, required: bool = False) -> Any:
    # An explicit `None` default bypasses click's required-option check.
    attrs: dict[str, Any] = {"required": True} if required else {"default": None}
    return click.option(
        "--since",
        type=WindowTime(),
        help="Only records at/after this instant: a bare time is read in the caller's own local time, "
        "one ending Z or +hh:mm is that instant.",
        **attrs,
    )


def until_option(*, required: bool = False) -> Any:
    # An explicit `None` default bypasses click's required-option check.
    attrs: dict[str, Any] = {"required": True} if required else {"default": None}
    return click.option(
        "--until",
        type=WindowTime(),
        help="Only records before this instant: a bare time is read in the caller's own local time, "
        "one ending Z or +hh:mm is that instant.",
        **attrs,
    )


def refuse_future_until(until: datetime, clock: IClock | None = None) -> None:
    """Refuse an ``--until`` past the caller's clock before any window is sent, so a range is never half told.
    A short-lived CLI process wires its own clock here; tests hand one in."""
    if until.astimezone(UTC) > (clock or SystemClock()).now():
        raise click.ClickException("--until must not be in the future")


def replay_windows(since: datetime, until: datetime, width_seconds: int | None) -> list[tuple[datetime, datetime]]:
    """``[since, until)`` as consecutive UTC windows of at most ``width_seconds``; one window if no width is known."""
    start, stop = since.astimezone(UTC), until.astimezone(UTC)
    if width_seconds is None or stop <= start:
        return [(start, stop)]
    width = timedelta(seconds=width_seconds)
    windows: list[tuple[datetime, datetime]] = []
    while start < stop:
        windows.append((start, min(start + width, stop)))
        start = windows[-1][1]
    return windows


def resume_since(window_start: datetime) -> str:
    """A window's start as a ``--since`` value, read in local time."""
    return window_start.astimezone().strftime("%Y-%m-%dT%H:%M:%S")
