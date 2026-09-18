"""The OpenCode ``IHarnessTranscriptSource`` adapter (blizzard#437), the OpenCode analogue of
``claude_code_transcript.py`` — named apart from ``opencode_transcript.py`` (the compatibility
proof's own identity-comparison module) to avoid colliding with it. Every call re-exports the
whole root session; :class:`~.opencode_cursor.MessagePartCursor` turns that into an incremental
read, and its token is this module's own opaque :class:`TranscriptPosition`."""

from __future__ import annotations

import json
from dataclasses import replace

from blizzard.runner.harness.internal.opencode_cursor import (
    CursorError,
    CursorRecord,
    MessagePartCursor,
    MessagePartIdentity,
    records_for_export,
)
from blizzard.runner.harness.internal.opencode_export import IOpenCodeExporter, OpenCodeExportError
from blizzard.runner.harness.internal.opencode_normalizer import (
    NORMALIZER_VERSION,
    ChildCandidate,
    build_turns,
    harness_version_of,
)
from blizzard.runner.harness.internal.opencode_shapes import (
    OpenCodeSessionExport,
    OpenCodeShapeError,
    parse_session_export,
)
from blizzard.runner.harness.transcript import (
    IHarnessTranscriptSource,
    SidechainConversation,
    TranscriptBatch,
    TranscriptErrorFactory,
    TranscriptPosition,
    TranscriptReadReason,
)

#: Every way `_export` can fail to produce a parsed export.
_EXPORT_ERRORS = (OpenCodeExportError, json.JSONDecodeError, OpenCodeShapeError)


class OpenCodeTranscriptSource:
    """Exports and normalizes a session's transcript, resolving child sessions into sidechains
    (``bzh:dependency-injection`` — ``exporter`` already closes over the configured binary).
    No confirmed signal distinguishes "no such session" from any other export failure (D2), so
    every export failure here reads as ``"unreadable"``, never a guessed ``"not_found"``."""

    def __init__(self, exporter: IOpenCodeExporter, error_factory: TranscriptErrorFactory) -> None:
        self._exporter = exporter
        self._errors = error_factory

    def turns_since(
        self, session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None
    ) -> TranscriptBatch:
        del spawn_cwd  # OpenCode's export resolves a session by id from any working directory
        try:
            cursor = MessagePartCursor.from_token(since.token if since is not None else None)
        except CursorError as exc:
            self._errors.from_io(exc, f"transcript cursor malformed: {session_id}", session_id=session_id)
            return self._unavailable(session_id, "unreadable")

        export = self._export(session_id, recovered=False)
        if export is None:
            return self._unavailable(session_id, "unreadable")

        records = records_for_export(export)
        read = cursor.admit(records)
        admitted = frozenset(admission.record.identity for admission in read.admissions)
        turns, child_candidates = build_turns(export.messages, admitted=admitted)
        for index, candidate in child_candidates.items():
            sidechain = self._resolve_sidechain(parent_session_id=export.info.id, candidate=candidate)
            if sidechain is not None:
                turns[index] = replace(turns[index], sidechain=sidechain)

        return TranscriptBatch(
            session_id=session_id,
            available=True,
            reason=None,
            turns=turns,
            # No route here ever produces one: a failed or mismatched child link shows
            # nothing this tick rather than an unlinked entry (never manufactures a parent link).
            unlinked_sidechains=[],
            next_position=TranscriptPosition(token=read.cursor.token),
            complete=True,
            truncated=False,
            sidechain_truncated=False,
            normalizer_version=NORMALIZER_VERSION,
            harness_version=harness_version_of(export),
        )

    def read_raw_lines(
        self,
        session_id: str,
        *,
        spawn_cwd: str | None,
        start: TranscriptPosition | None = None,
        end: TranscriptPosition | None = None,
    ) -> list[str]:
        del spawn_cwd
        export = self._export(session_id, recovered=True)
        if export is None:
            return []
        records = records_for_export(export)
        try:
            in_range = self._in_range_identities(records, start=start, end=end)
        except CursorError:
            return []

        lines: list[str] = []
        for message in export.messages:
            if message.info.role != "assistant" or not any(p.type == "step-finish" for p in message.parts):
                continue
            identities = frozenset(MessagePartIdentity(message.info.id, part.id) for part in message.parts)
            if identities & in_range:
                lines.append(json.dumps(message.raw))
        return lines

    @staticmethod
    def _in_range_identities(
        records: tuple[CursorRecord, ...], *, start: TranscriptPosition | None, end: TranscriptPosition | None
    ) -> frozenset[MessagePartIdentity]:
        """``[start, end)`` decoded as identity sets; ``None, None`` names every identity the
        export currently carries — the whole-session read a caller wanting "today" passes."""
        if start is None and end is None:
            return frozenset(record.identity for record in records)
        if end is not None:
            end_cursor = MessagePartCursor.from_token(end.token)
        else:
            end_cursor = MessagePartCursor.start().admit(records).cursor
        start_cursor = MessagePartCursor.from_token(start.token) if start is not None else MessagePartCursor.start()
        end_marks = frozenset(mark.identity for mark in end_cursor.marks)
        start_marks = frozenset(mark.identity for mark in start_cursor.marks)
        return end_marks - start_marks

    def tail_position(self, session_id: str, *, spawn_cwd: str | None) -> TranscriptPosition | None:
        del spawn_cwd
        export = self._export(session_id, recovered=True)
        if export is None:
            return None
        cursor = MessagePartCursor.start().admit(records_for_export(export)).cursor
        return TranscriptPosition(token=cursor.token)

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        """The raw exporter output's byte length, before any JSON re-parsing — the serialized
        root-session representation this source actually reads, child sessions excluded."""
        del spawn_cwd
        try:
            raw = self._exporter.export(session_id)
        except OpenCodeExportError as exc:
            self._errors.from_io_recovered(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return None
        return len(raw.encode("utf-8"))

    def context_tokens(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        """Unconditionally unknown: which OpenCode token fields represent prompt-context size
        is unverified until the compatibility proof establishes it (spec, "Forward reads")."""
        del session_id, spawn_cwd
        return None

    def _export(self, session_id: str, *, recovered: bool) -> OpenCodeSessionExport | None:
        try:
            raw = self._exporter.export(session_id)
            return parse_session_export(json.loads(raw))
        except _EXPORT_ERRORS as exc:
            message = f"transcript unreadable: {session_id}"
            if recovered:
                self._errors.from_io_recovered(exc, message, session_id=session_id)
            else:
                self._errors.from_io(exc, message, session_id=session_id)
            return None

    def _resolve_sidechain(
        self, *, parent_session_id: str, candidate: ChildCandidate
    ) -> SidechainConversation | None:
        try:
            raw = self._exporter.export(candidate.session_id)
            child_export = parse_session_export(json.loads(raw))
        except _EXPORT_ERRORS as exc:
            self._errors.from_io_recovered(
                exc,
                "child session export unreadable",
                session_id=parent_session_id,
                child_session_id=candidate.session_id,
            )
            return None
        if child_export.info.parent_id != parent_session_id:
            return None  # not this session's own child — expected, not an error
        child_turns, _ = build_turns(child_export.messages, admitted=None)
        return SidechainConversation(
            agent_id=candidate.session_id, agent_type=candidate.agent_type, link="session-id", turns=child_turns
        )

    @staticmethod
    def _unavailable(session_id: str, reason: TranscriptReadReason) -> TranscriptBatch:
        return TranscriptBatch(
            session_id=session_id,
            available=False,
            reason=reason,
            turns=[],
            unlinked_sidechains=[],
            next_position=None,
            complete=True,
            truncated=False,
            sidechain_truncated=False,
            normalizer_version=NORMALIZER_VERSION,
            harness_version=None,
        )


# Typecheck-time conformance sentinel (`blizzard-context:/exemplars/python/`): the return is
# rejected if this class drifts from the Protocol.
def _conforms_harness_transcript_source(x: OpenCodeTranscriptSource) -> IHarnessTranscriptSource:
    return x
