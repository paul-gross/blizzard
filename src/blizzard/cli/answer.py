"""Decode a daemon's answer without exposing its contents or credentials in CLI diagnostics."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import click
from pydantic import TypeAdapter, ValidationError

from blizzard.cli.collaborators import Response


def peer_address(where: str) -> str:
    """The socket or URL origin identifying a daemon, without URL credentials or other secrets."""
    if "://" not in where:
        return where
    parts = urlsplit(where)
    return f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}"


def decode_answer[T](
    response: Response, adapter: TypeAdapter[T], *, verb: str, peer: str, where: str, remedy: str | None = None
) -> tuple[Any, T]:
    """Return both the original JSON value and its validated interpretation.

    A failure names ``peer`` at the sanitized ``where`` and carries only an invalid-JSON note or a validation-error
    count, never a value from the answer. ``remedy``, when given, follows the address as ``— <remedy>``.
    """
    where = peer_address(where)
    try:
        raw = response.json()
    except (ValueError, UnicodeDecodeError):
        raise click.ClickException(_unreadable(verb, peer, where, remedy, "invalid JSON")) from None
    try:
        value = adapter.validate_python(raw)
    except ValidationError as exc:
        count = exc.error_count()
        summary = f"{count} validation {'error' if count == 1 else 'errors'}"
        raise click.ClickException(_unreadable(verb, peer, where, remedy, summary)) from None
    return raw, value


def _unreadable(verb: str, peer: str, where: str, remedy: str | None, summary: str) -> str:
    advice = f" — {remedy}" if remedy else ""
    return f"{verb}: unreadable answer from {peer} at {where}{advice} ({summary})"
