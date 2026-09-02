"""Identity-based forward admission for OpenCode messages and parts.

The cursor has no array offset.  It remembers stable ``(message_id, part_id)`` identities and the
last fingerprint admitted for each one, so a pending tool state can be patched by its later
completed state while compaction can remove retained history without moving the cursor backward.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from blizzard.runner.harness.internal.opencode_shapes import OpenCodeSessionExport

CURSOR_VERSION = 1


class CursorError(ValueError):
    """A cursor token is malformed or contains an unsupported required shape."""


@dataclass(frozen=True)
class MessagePartIdentity:
    """The stable identity used for forward admission."""

    message_id: str
    part_id: str | None

    def __post_init__(self) -> None:
        if not self.message_id:
            raise CursorError("message identity must be non-empty")
        if self.part_id == "":
            raise CursorError("part identity must be non-empty when present")


@dataclass(frozen=True)
class CursorRecord:
    """One identity and its current shape, ready for admission."""

    identity: MessagePartIdentity
    payload: object
    fingerprint: str

    @classmethod
    def of(
        cls,
        message_id: str,
        part_id: str | None,
        payload: object,
    ) -> CursorRecord:
        return cls(MessagePartIdentity(message_id, part_id), payload, _fingerprint(payload))


@dataclass(frozen=True)
class CursorMark:
    """The opaque cursor's remembered identity and latest admitted revision."""

    identity: MessagePartIdentity
    fingerprint: str


@dataclass(frozen=True)
class CursorAdmission:
    """A record admitted as genuinely new or as a state update to a known identity."""

    record: CursorRecord
    kind: Literal["new", "updated"]


@dataclass(frozen=True)
class CursorRead:
    """The forward delta and the cursor to persist for the next read."""

    admissions: tuple[CursorAdmission, ...]
    cursor: MessagePartCursor

    @property
    def records(self) -> tuple[CursorRecord, ...]:
        """The admitted records in current export order."""

        return tuple(admission.record for admission in self.admissions)


@dataclass(frozen=True)
class MessagePartCursor:
    """An opaque, identity-based cursor with deterministic JSON serialization."""

    marks: tuple[CursorMark, ...] = ()

    def __post_init__(self) -> None:
        identities: set[MessagePartIdentity] = set()
        for mark in self.marks:
            if not mark.fingerprint:
                raise CursorError("cursor mark fingerprint must be non-empty")
            if mark.identity in identities:
                raise CursorError(f"cursor contains duplicate identity {mark.identity!r}")
            identities.add(mark.identity)

    @classmethod
    def start(cls) -> MessagePartCursor:
        return cls()

    @classmethod
    def from_token(cls, token: str | None) -> MessagePartCursor:
        """Decode a token minted by this module; malformed tokens fail explicitly."""

        if token is None:
            return cls.start()
        try:
            decoded = json.loads(token)
        except json.JSONDecodeError as exc:
            raise CursorError("cursor token is not valid JSON") from exc
        if not isinstance(decoded, dict):
            raise CursorError("cursor token must be an object")
        if decoded.get("version") != CURSOR_VERSION:
            raise CursorError(f"unsupported cursor version: {decoded.get('version')!r}")
        seen = decoded.get("seen")
        if not isinstance(seen, list):
            raise CursorError("cursor token 'seen' must be an array")
        marks: list[CursorMark] = []
        for index, raw_mark in enumerate(seen):
            if not isinstance(raw_mark, dict):
                raise CursorError(f"cursor token seen[{index}] must be an object")
            message_id = raw_mark.get("message_id")
            part_id = raw_mark.get("part_id")
            fingerprint = raw_mark.get("fingerprint")
            if not isinstance(message_id, str) or not message_id:
                raise CursorError(f"cursor token seen[{index}].message_id must be a non-empty string")
            if part_id is not None and (not isinstance(part_id, str) or not part_id):
                raise CursorError(f"cursor token seen[{index}].part_id must be a non-empty string or null")
            if not isinstance(fingerprint, str) or not fingerprint:
                raise CursorError(f"cursor token seen[{index}].fingerprint must be a non-empty string")
            marks.append(CursorMark(MessagePartIdentity(message_id, part_id), fingerprint))
        return cls(tuple(marks))

    @property
    def token(self) -> str:
        """The persisted opaque token; it names identities, never array positions."""

        return json.dumps(
            {
                "version": CURSOR_VERSION,
                "seen": [
                    {
                        "message_id": mark.identity.message_id,
                        "part_id": mark.identity.part_id,
                        "fingerprint": mark.fingerprint,
                    }
                    for mark in self.marks
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def admit(self, records: Iterable[CursorRecord]) -> CursorRead:
        """Admit new identities and changed revisions, preserving current order.
        Repeated identities inside one export collapse to their last state while retaining the
        identity's first position.  That mirrors an export's final pending-to-complete state and
        prevents duplicate identities from reaching the transcript lane.
        """

        current: dict[MessagePartIdentity, CursorRecord] = {}
        for record in records:
            current[record.identity] = record

        marks = {mark.identity: mark for mark in self.marks}
        admissions: list[CursorAdmission] = []
        for record in current.values():
            previous = marks.get(record.identity)
            if previous is None:
                marks[record.identity] = CursorMark(record.identity, record.fingerprint)
                admissions.append(CursorAdmission(record, "new"))
            elif previous.fingerprint != record.fingerprint:
                marks[record.identity] = CursorMark(record.identity, record.fingerprint)
                admissions.append(CursorAdmission(record, "updated"))

        ordered_marks = tuple(marks.values())
        return CursorRead(tuple(admissions), MessagePartCursor(ordered_marks))


def records_for_export(export: OpenCodeSessionExport) -> tuple[CursorRecord, ...]:
    """Flatten a parsed export into message/part identity records in stable source order."""

    records: list[CursorRecord] = []
    for message in export.messages:
        if not message.parts:
            records.append(CursorRecord.of(message.info.id, None, message.raw))
            continue
        for part in message.parts:
            records.append(CursorRecord.of(message.info.id, part.id, part.raw))
    return tuple(records)


def _fingerprint(value: object) -> str:
    try:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        canonical = repr(value)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "CURSOR_VERSION",
    "CursorAdmission",
    "CursorError",
    "CursorMark",
    "CursorRead",
    "CursorRecord",
    "MessagePartCursor",
    "MessagePartIdentity",
    "records_for_export",
]
