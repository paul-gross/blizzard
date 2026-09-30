"""The ``--since``/``--until`` window flags CLI verbs share — one declaration of each
flag, read in the caller's local time and converted to UTC for the wire."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import click

from blizzard.foundation.store.utc import iso_utc


def utc_query_value(value: datetime | None) -> str | None:
    """A bare ``--since``/``--until`` is read as the caller's own local wall clock, not
    UTC — converted (not merely relabeled) before it crosses the wire."""
    return iso_utc(value.astimezone(UTC)) if value is not None else None


def since_option(*, required: bool = False) -> Any:
    # `default` is left unset (click's own UNSET sentinel) rather than passed as `None`
    # when `required` — an explicit `None` default reads as "the caller supplied None",
    # not "missing", so click's own required check would never fire.
    attrs: dict[str, Any] = {"required": True} if required else {"default": None}
    return click.option(
        "--since",
        type=click.DateTime(),
        help="Only records at/after this instant, read in the caller's own local time.",
        **attrs,
    )


def until_option(*, required: bool = False) -> Any:
    # `default` is left unset (click's own UNSET sentinel) rather than passed as `None`
    # when `required` — an explicit `None` default reads as "the caller supplied None",
    # not "missing", so click's own required check would never fire.
    attrs: dict[str, Any] = {"required": True} if required else {"default": None}
    return click.option(
        "--until",
        type=click.DateTime(),
        help="Only records before this instant, read in the caller's own local time.",
        **attrs,
    )
