"""Decode runner answers, naming the runner and how to recover from an unreadable one."""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from blizzard.cli import answer
from blizzard.cli.collaborators import Response

PEER = "the runner"
REMEDY = "restart it on this build"


def decode_answer[T](response: Response, adapter: TypeAdapter[T], *, verb: str, where: str) -> tuple[Any, T]:
    """Return both the original JSON value and its validated interpretation, as the runner's answer."""
    return answer.decode_answer(response, adapter, verb=verb, peer=PEER, where=where, remedy=REMEDY)
