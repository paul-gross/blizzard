"""Decode runner answers without exposing their contents in CLI diagnostics."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import click
from pydantic import TypeAdapter, ValidationError

from blizzard.cli.collaborators import Response


def runner_address(where: str) -> str:
    """The socket or URL origin identifying the runner, without URL credentials or other secrets."""
    if "://" not in where:
        return where
    parts = urlsplit(where)
    return f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}"


def decode_answer[T](response: Response, adapter: TypeAdapter[T], *, verb: str, where: str) -> tuple[Any, T]:
    """Return both the original JSON value and its validated interpretation."""
    where = runner_address(where)
    try:
        raw = response.json()
    except (ValueError, UnicodeDecodeError):
        raise click.ClickException(
            f"{verb}: unreadable answer from the runner at {where} — restart it on this build (invalid JSON)"
        ) from None
    try:
        value = adapter.validate_python(raw)
    except ValidationError as exc:
        count = exc.error_count()
        summary = f"{count} validation {'error' if count == 1 else 'errors'}"
        raise click.ClickException(
            f"{verb}: unreadable answer from the runner at {where} — restart it on this build ({summary})"
        ) from None
    return raw, value
