"""The OpenCode 2 transcript source's opaque position: for the root and each descendant read so far, the service's own
listing cursor, the last message admitted, and every admitted message still open — how many of its parts shipped and
which shipped calls still wait for their output. A token another source minted is refused."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.json_value import JsonObject, JsonValue, decode_json

#: Names this source's tokens; a token without it is another source's.
POSITION_SCHEMA = "opencode-v2-transcript/1"


class PositionError(ValueError):
    """A token this source did not mint, or one it cannot read."""


@domain_model
@dataclass(frozen=True)
class OpenMessage:
    """An admitted message still being written: ``shipped`` of its parts are out, and the ``pending`` calls among
    them shipped before their output."""

    shipped: int
    pending: tuple[str, ...] = ()


@domain_model
@dataclass(frozen=True)
class SessionMark:
    """How far one session was read: ``cursor`` resumes the listing after ``last``; ``None`` for both is unread."""

    cursor: str | None = None
    last: str | None = None
    open: Mapping[str, OpenMessage] = field(default_factory=dict[str, OpenMessage])


@domain_model
@dataclass(frozen=True)
class TranscriptMark:
    sessions: Mapping[str, SessionMark] = field(default_factory=dict[str, SessionMark])

    def of(self, session_id: str) -> SessionMark:
        return self.sessions.get(session_id, SessionMark())

    @property
    def token(self) -> str:
        sessions: JsonObject = {}
        for session_id, mark in sorted(self.sessions.items()):
            sessions[session_id] = {
                "cursor": mark.cursor,
                "last": mark.last,
                "open": {
                    message_id: {"shipped": message.shipped, "pending": list[JsonValue](message.pending)}
                    for message_id, message in sorted(mark.open.items())
                },
            }
        return json.dumps({"schema": POSITION_SCHEMA, "sessions": sessions}, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_token(cls, token: str | None) -> TranscriptMark:
        if token is None:
            return cls()
        try:
            decoded = decode_json(token)
        except ValueError as exc:
            raise PositionError("the transcript position is not JSON") from exc
        if not isinstance(decoded, dict) or decoded.get("schema") != POSITION_SCHEMA:
            raise PositionError("the transcript position was not minted by the OpenCode 2 source")
        sessions = decoded.get("sessions")
        if not isinstance(sessions, dict):
            raise PositionError("the transcript position names no sessions")
        return cls({session_id: _mark(value) for session_id, value in sessions.items()})


def _optional_text(record: JsonObject, key: str) -> str | None:
    value = record.get(key)
    if value is not None and not isinstance(value, str):
        raise PositionError(f"the transcript position's {key} is not text")
    return value


def _mark(value: JsonValue) -> SessionMark:
    if not isinstance(value, dict):
        raise PositionError("a session's transcript position is not an object")
    opened = value.get("open", {})
    if not isinstance(opened, dict):
        raise PositionError("a session's open messages are not an object")
    return SessionMark(
        cursor=_optional_text(value, "cursor"),
        last=_optional_text(value, "last"),
        open={message_id: _open(message) for message_id, message in opened.items()},
    )


def _open(value: JsonValue) -> OpenMessage:
    if not isinstance(value, dict):
        raise PositionError("an open message's position is not an object")
    shipped, pending = value.get("shipped"), value.get("pending", [])
    if not isinstance(shipped, int) or isinstance(shipped, bool) or shipped < 0:
        raise PositionError("an open message's shipped count is not a count")
    if not isinstance(pending, list) or not all(isinstance(call, str) for call in pending):
        raise PositionError("an open message's pending calls are not text")
    return OpenMessage(shipped, tuple(call for call in pending if isinstance(call, str)))


__all__ = ["POSITION_SCHEMA", "OpenMessage", "PositionError", "SessionMark", "TranscriptMark"]
