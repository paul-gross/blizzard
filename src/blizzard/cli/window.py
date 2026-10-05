"""The ``--since``/``--until`` window flags CLI verbs share — one declaration of each
flag, read in the caller's local time and converted to UTC for the wire."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, overload

import click

from blizzard.foundation.store.utc import iso_utc


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
        type=click.DateTime(),
        help="Only records at/after this instant, read in the caller's own local time.",
        **attrs,
    )


def until_option(*, required: bool = False) -> Any:
    # An explicit `None` default bypasses click's required-option check.
    attrs: dict[str, Any] = {"required": True} if required else {"default": None}
    return click.option(
        "--until",
        type=click.DateTime(),
        help="Only records before this instant, read in the caller's own local time.",
        **attrs,
    )


def utc_now() -> datetime:
    """The caller's clock — the seam tests move."""
    return datetime.now(UTC)


def refuse_future_until(until: datetime) -> None:
    """Refuse an ``--until`` past the caller's clock before any window is sent, so a range is never half told."""
    if until.astimezone(UTC) > utc_now():
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
