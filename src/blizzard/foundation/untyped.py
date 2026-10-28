"""Narrowing for values that enter untyped — a decoded document, a config entry, a header body.

``isinstance(value, dict)`` on an ``object`` yields ``dict[Unknown, Unknown]``; these two answer with the element
types known, in the one place that has to say so. Each returns the very container it was given, so a caller may edit
it in place. JSON and TOML objects are string-keyed by construction."""

from __future__ import annotations

from typing import cast


def as_table(value: object) -> dict[str, object] | None:
    """``value`` as a string-keyed table, or ``None`` when it is not a dict."""
    if not isinstance(value, dict):
        return None
    return cast("dict[str, object]", value)


def as_list(value: object) -> list[object] | None:
    """``value`` as a list, or ``None`` when it is not one."""
    if not isinstance(value, list):
        return None
    return cast("list[object]", value)
