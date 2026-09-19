"""The OpenCode ``IHarnessTranscriptSource`` adapter (blizzard#437), the OpenCode analogue of
``claude_code_transcript.py`` — named apart from ``opencode_transcript.py`` (the compatibility
proof's own identity-comparison module) to avoid colliding with it. Every call re-exports the
whole root session; :class:`~.opencode_cursor.MessagePartCursor` turns that into an incremental
read, and its token is this module's own opaque :class:`TranscriptPosition`."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from blizzard.runner.harness.internal.opencode_cursor import (
    CursorAdmission,
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
    child_candidate_of,
    harness_version_of,
    late_tool_output_of,
)
from blizzard.runner.harness.internal.opencode_shapes import (
    OpenCodeMessage,
    OpenCodePart,
    OpenCodeSessionExport,
    OpenCodeShapeError,
    parse_session_export,
)
from blizzard.runner.harness.transcript import (
    IHarnessTranscriptSource,
    LateToolOutput,
    SidechainConversation,
    TranscriptBatch,
    TranscriptErrorFactory,
    TranscriptPosition,
    TranscriptReadReason,
)

#: Every way `_export` can fail to produce a parsed export.
_EXPORT_ERRORS = (OpenCodeExportError, json.JSONDecodeError, OpenCodeShapeError)


@dataclass(frozen=True)
class _Position:
    """This source's own opaque :class:`TranscriptPosition` token: the identity cursor plus
    which child sessions are already linked, kept apart from D1's own pruning bound — a
    linked child is cross-session bookkeeping, never a compactable identity. A token minted
    before this field existed decodes as ``linked_children=frozenset()``, never ``unreadable``."""

    cursor: MessagePartCursor
    linked_children: frozenset[str]

    @classmethod
    def start(cls) -> _Position:
        return cls(MessagePartCursor.start(), frozenset())

    @classmethod
    def from_token(cls, token: str | None) -> _Position:
        if token is None:
            return cls.start()
        try:
            decoded = json.loads(token)
        except json.JSONDecodeError as exc:
            raise CursorError("cursor token is not valid JSON") from exc
        if not isinstance(decoded, dict):
            raise CursorError("cursor token must be an object")
        if "cursor" not in decoded and "seen" in decoded:
            # A bare `MessagePartCursor` token, minted before this field existed.
            return cls(MessagePartCursor.from_token(token), frozenset())
        cursor_raw = decoded.get("cursor")
        cursor = MessagePartCursor.from_token(json.dumps(cursor_raw) if cursor_raw is not None else None)
        linked_raw = decoded.get("linked_children", [])
        if not isinstance(linked_raw, list) or not all(isinstance(x, str) and x for x in linked_raw):
            raise CursorError("cursor token 'linked_children' must be an array of non-empty strings")
        return cls(cursor, frozenset(linked_raw))

    @property
    def token(self) -> str:
        return json.dumps(
            {"cursor": json.loads(self.cursor.token), "linked_children": sorted(self.linked_children)},
            sort_keys=True,
            separators=(",", ":"),
        )


def _parts_by_identity(messages: tuple[OpenCodeMessage, ...]) -> dict[MessagePartIdentity, OpenCodePart]:
    return {MessagePartIdentity(message.info.id, part.id): part for message in messages for part in message.parts}


def _split_admissions(
    admissions: tuple[CursorAdmission, ...], parts_by_identity: dict[MessagePartIdentity, OpenCodePart]
) -> tuple[frozenset[MessagePartIdentity], list[LateToolOutput]]:
    """Split ``admissions`` into the identities :func:`build_turns` should build fresh turns
    for, and the late-output patches for a tool part the cursor admits as ``"updated"`` — a
    previously-shipped call's pending state resolving to a result, patched onto the earlier
    call rather than re-emitted as a second, full turn (review F3). A revision with no output
    yet (e.g. pending to running) ships nothing: nothing to build, nothing to patch."""
    admitted: set[MessagePartIdentity] = set()
    late_outputs: list[LateToolOutput] = []
    for admission in admissions:
        identity = admission.record.identity
        part = parts_by_identity.get(identity)
        if admission.kind == "updated" and part is not None and part.type == "tool":
            late = late_tool_output_of(part)
            if late is not None:
                late_outputs.append(late)
            continue
        admitted.add(identity)
    return frozenset(admitted), late_outputs


@dataclass(frozen=True)
class _ChildLinkResult:
    """One tick's child-session linking pass — see
    :meth:`OpenCodeTranscriptSource._link_children`."""

    agent_tool_use_ids: dict[str, str]
    resolved_by_identity: dict[MessagePartIdentity, SidechainConversation]
    unresolved: list[SidechainConversation]
    newly_linked: frozenset[str]


class OpenCodeTranscriptSource:
    """Exports and normalizes a session's transcript, resolving child sessions into sidechains
    (``bzh:dependency-injection`` — ``exporter`` already closes over the configured binary).
    No confirmed signal distinguishes "no such session" from any other export failure (D2), so
    every export failure here reads as ``"unreadable"``, never a guessed ``"not_found"``."""

    def __init__(self, exporter: IOpenCodeExporter, error_factory: TranscriptErrorFactory) -> None:
        self._exporter = exporter
        self._errors = error_factory
        self._memo: tuple[str, str] | None = None  # see `_fetch`/`_reuse_or_fetch` (review F17)

    def _fetch(self, session_id: str) -> str:
        """A genuinely fresh export, offered as the memo to the VERY NEXT
        :meth:`_reuse_or_fetch` call for the same session (review F17). A raising fetch never
        reaches the write, so a failure can never poison a later read."""
        raw = self._exporter.export(session_id)
        self._memo = (session_id, raw)
        return raw

    def _reuse_or_fetch(self, session_id: str) -> str:
        """``_fetch``'s memoized counterpart, single-use: the memo the immediately preceding
        call left, when it names this same session, else a fresh fetch. Coalesces
        ``tail_position`` immediately followed by ``read_raw_lines`` (``_worker_sample``'s own
        shape) into one real export. Never called by ``turns_since``/``tail_position``
        themselves — a forward read or boundary anchor must never be one call stale."""
        if self._memo is not None and self._memo[0] == session_id:
            raw = self._memo[1]
            self._memo = None
            return raw
        return self._fetch(session_id)

    def turns_since(
        self, session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None
    ) -> TranscriptBatch:
        del spawn_cwd  # OpenCode's export resolves a session by id from any working directory
        try:
            position = _Position.from_token(since.token if since is not None else None)
        except CursorError as exc:
            self._errors.from_io(exc, f"transcript cursor malformed: {session_id}", session_id=session_id)
            return self._unavailable(session_id, "unreadable")

        export = self._export(session_id, recovered=False, fresh=True)  # never one tick stale (F17)
        if export is None:
            return self._unavailable(session_id, "unreadable")

        records = records_for_export(export)
        parts_by_identity = _parts_by_identity(export.messages)
        # Runs over the WHOLE export every tick, independent of cursor admission (F4): a
        # child's export can fail or momentarily mismatch, and must stay visible and retryable.
        children = self._link_children(
            export, parts_by_identity=parts_by_identity, already_linked=position.linked_children
        )

        read = position.cursor.admit(records)
        admitted, late_tool_outputs = _split_admissions(read.admissions, parts_by_identity)
        turns, tool_turns = build_turns(export.messages, admitted=admitted)

        unlinked_sidechains = list(children.unresolved)
        for identity, sidechain in children.resolved_by_identity.items():
            index = tool_turns.get(identity)
            if index is not None:
                turns[index] = replace(turns[index], sidechain=sidechain)
            else:
                # No turn for the spawning call this tick (already shipped earlier) — the
                # pump's own cross-window agent-id route attaches it instead (blizzard#338).
                unlinked_sidechains.append(replace(sidechain, link="unlinked"))

        next_position = _Position(read.cursor, position.linked_children | children.newly_linked)
        return TranscriptBatch(
            session_id=session_id,
            available=True,
            reason=None,
            turns=turns,
            unlinked_sidechains=unlinked_sidechains,
            next_position=TranscriptPosition(token=next_position.token),
            complete=True,
            truncated=False,
            sidechain_truncated=False,
            normalizer_version=NORMALIZER_VERSION,
            harness_version=harness_version_of(export),
            late_tool_outputs=late_tool_outputs,
            agent_tool_use_ids=children.agent_tool_use_ids,
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
            end_cursor = _Position.from_token(end.token).cursor
        else:
            end_cursor = MessagePartCursor.start().admit(records).cursor
        start_cursor = _Position.from_token(start.token).cursor if start is not None else MessagePartCursor.start()
        end_marks = frozenset(mark.identity for mark in end_cursor.marks)
        start_marks = frozenset(mark.identity for mark in start_cursor.marks)
        return end_marks - start_marks

    def tail_position(self, session_id: str, *, spawn_cwd: str | None) -> TranscriptPosition | None:
        del spawn_cwd
        export = self._export(session_id, recovered=True, fresh=True)  # a boundary anchor (F17)
        if export is None:
            return None
        cursor = MessagePartCursor.start().admit(records_for_export(export)).cursor
        return TranscriptPosition(token=_Position(cursor, frozenset()).token)

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        """The raw exporter output's byte length, before any JSON re-parsing — the serialized
        root-session representation this source actually reads, child sessions excluded."""
        del spawn_cwd
        try:
            raw = self._reuse_or_fetch(session_id)
        except OpenCodeExportError as exc:
            self._errors.from_io_recovered(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return None
        return len(raw.encode("utf-8"))

    def context_tokens(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        """Unconditionally unknown: which OpenCode token fields represent prompt-context size
        is unverified until the compatibility proof establishes it (spec, "Forward reads")."""
        del session_id, spawn_cwd
        return None

    def _export(self, session_id: str, *, recovered: bool, fresh: bool = False) -> OpenCodeSessionExport | None:
        try:
            raw = self._fetch(session_id) if fresh else self._reuse_or_fetch(session_id)
            return parse_session_export(json.loads(raw))
        except _EXPORT_ERRORS as exc:
            message = f"transcript unreadable: {session_id}"
            if recovered:
                self._errors.from_io_recovered(exc, message, session_id=session_id)
            else:
                self._errors.from_io(exc, message, session_id=session_id)
            return None

    def _link_children(
        self,
        export: OpenCodeSessionExport,
        *,
        parts_by_identity: dict[MessagePartIdentity, OpenCodePart],
        already_linked: frozenset[str],
    ) -> _ChildLinkResult:
        """Every child-session candidate the CURRENT export carries, resolved (or retried)
        this tick. ``already_linked`` names every child this segment has already resolved and
        surfaced on an earlier tick — skipped without a fetch, never re-attempted or
        re-surfaced: the one guard that keeps this retry loop from shipping the same
        conversation twice once it finally succeeds."""
        agent_tool_use_ids: dict[str, str] = {}
        resolved_by_identity: dict[MessagePartIdentity, SidechainConversation] = {}
        unresolved: list[SidechainConversation] = []
        newly_linked: set[str] = set()
        for identity, part in parts_by_identity.items():
            if part.type != "tool":
                continue
            candidate = child_candidate_of(part)
            if candidate is None or candidate.session_id in already_linked:
                continue
            sidechain = self._resolve_sidechain(parent_session_id=export.info.id, candidate=candidate)
            if sidechain is None:
                # Visible, not lost (F4): a failure or `parentID` mismatch retries every
                # tick, rather than being silently dropped the one time it is seen.
                unresolved.append(
                    SidechainConversation(
                        agent_id=candidate.session_id, agent_type=candidate.agent_type, link="unlinked", turns=[]
                    )
                )
                continue
            assert part.call_id is not None  # OpenCodePart.parse requires callID on every tool part
            agent_tool_use_ids[candidate.session_id] = part.call_id
            newly_linked.add(candidate.session_id)
            resolved_by_identity[identity] = sidechain
        return _ChildLinkResult(agent_tool_use_ids, resolved_by_identity, unresolved, frozenset(newly_linked))

    def _resolve_sidechain(self, *, parent_session_id: str, candidate: ChildCandidate) -> SidechainConversation | None:
        try:
            raw = self._reuse_or_fetch(candidate.session_id)
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
