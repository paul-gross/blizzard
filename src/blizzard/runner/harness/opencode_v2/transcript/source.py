"""The OpenCode 2 ``IHarnessTranscriptSource``: reads a session tree through the service's read operations alone, so
a read never starts or continues a turn. Each session is read forward from the service's own cursor and its open steps
are read again by id; a child is a sidechain of the call that spawned it. A session this runner did not mint is
``not_found``, a position this source did not mint ``unreadable``."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from blizzard.foundation.roles import adapter_model
from blizzard.runner.harness.json_value import JsonObject
from blizzard.runner.harness.opencode_v2.client import MESSAGE_PAGE_LIMIT, OpenCodeServiceClient, ServiceRequestError
from blizzard.runner.harness.opencode_v2.driver import BIND_FAULTS, RegistrationSource, ServiceNotRegisteredError
from blizzard.runner.harness.opencode_v2.request import minted_by
from blizzard.runner.harness.opencode_v2.shapes import Message, OpenCodeTwoShapeError, parse_message, parse_session
from blizzard.runner.harness.opencode_v2.transcript.normalizer import (
    NORMALIZER_VERSION,
    SUBAGENT_TOOL,
    is_open,
    late_outputs,
    message_turns,
    shippable,
    spawned_children,
    tool_output,
)
from blizzard.runner.harness.opencode_v2.transcript.position import (
    OpenMessage,
    PositionError,
    SessionMark,
    TranscriptMark,
)
from blizzard.runner.harness.opencode_v2.transport import BoundedTransport, IServiceTransport
from blizzard.runner.harness.transcript import (
    IHarnessTranscriptSource,
    LateToolOutput,
    NormalizedTurn,
    SidechainConversation,
    TranscriptBatch,
    TranscriptErrorFactory,
    TranscriptPosition,
    TranscriptReadReason,
)

#: One read's whole bound against the service.
DEFAULT_READ_BOUND_SECONDS = 10.0
#: Pages one forward read follows per session before it reports itself incomplete.
DEFAULT_PAGE_BUDGET = 25
#: How deep a session tree is read.
MAX_TREE_DEPTH = 8

_READ_FAULTS: tuple[type[Exception], ...] = (*BIND_FAULTS, OpenCodeTwoShapeError, OSError)


class _NotOursError(LookupError):
    """The session is missing, or another runner minted it."""


@adapter_model
@dataclass(frozen=True)
class _Session:
    id: str
    parent: str | None


@adapter_model
@dataclass
class _Listing:
    """Messages read forward from a cursor, the cursor after them, and whether the page budget ran out first."""

    raw: list[JsonObject] = field(default_factory=list[JsonObject])
    cursor: str | None = None
    exhausted: bool = False


@adapter_model
@dataclass
class _SessionRead:
    """One session's share of a forward read."""

    turns: list[NormalizedTurn] = field(default_factory=list[NormalizedTurn])
    calls: dict[str, int] = field(default_factory=dict[str, int])
    late: list[LateToolOutput] = field(default_factory=list[LateToolOutput])
    children: dict[str, str] = field(default_factory=dict[str, str])
    agents: dict[str, str] = field(default_factory=dict[str, str])
    mark: SessionMark = field(default_factory=SessionMark)
    exhausted: bool = False

    def extend(self, made: list[NormalizedTurn], calls: Mapping[str, int]) -> None:
        offset = len(self.turns)
        self.turns.extend(made)
        self.calls.update({call_id: offset + index for call_id, index in calls.items()})


class OpenCodeTwoTranscriptSource:
    def __init__(
        self,
        source: RegistrationSource,
        transport: IServiceTransport,
        errors: TranscriptErrorFactory,
        *,
        runner_id: str,
        read_bound_seconds: float = DEFAULT_READ_BOUND_SECONDS,
        page_budget: int = DEFAULT_PAGE_BUDGET,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._source = source
        self._transport = transport
        self._errors = errors
        self._runner_id = runner_id
        self._read_bound_seconds = read_bound_seconds
        self._page_budget = page_budget
        self._monotonic = monotonic

    # -- the seam ---------------------------------------------------------------------------------------------------

    def turns_since(
        self, session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None
    ) -> TranscriptBatch:
        del spawn_cwd  # the service finds a session by id from anywhere
        try:
            mark = TranscriptMark.from_token(since.token if since is not None else None)
        except PositionError as exc:
            self._errors.from_io(exc, f"transcript position refused: {session_id}", session_id=session_id)
            return _unavailable(session_id, "unreadable")
        try:
            client = self._client()
            tree = self._tree(client, session_id)
            reads = {session.id: self._read_session(client, session.id, mark.of(session.id)) for session in tree}
        except _NotOursError:
            self._errors.not_found(session_id=session_id)
            return _unavailable(session_id, "not_found")
        except _READ_FAULTS as exc:
            self._errors.from_io(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return _unavailable(session_id, "unreadable")
        return self._batch(session_id, tree, reads, mark, client.registration.version)

    def read_raw_lines(
        self,
        session_id: str,
        *,
        spawn_cwd: str | None,
        start: TranscriptPosition | None = None,
        end: TranscriptPosition | None = None,
    ) -> list[str]:
        """One record per message of the root and every descendant admitted after ``start`` through ``end``."""
        del spawn_cwd
        try:
            first = TranscriptMark.from_token(start.token if start is not None else None)
            last = TranscriptMark.from_token(end.token) if end is not None else None
            client = self._client()
            tree = self._tree(client, session_id)
            lines: list[str] = []
            for session in tree:
                listed = self._list(client, session.id, None, budget=None).raw
                lines.extend(
                    _record(session, message) for message in _between(listed, first.of(session.id), last, session.id)
                )
        except (PositionError, _NotOursError, *_READ_FAULTS) as exc:
            self._errors.from_io_recovered(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return []
        return lines

    def tail_position(self, session_id: str, *, spawn_cwd: str | None) -> TranscriptPosition | None:
        del spawn_cwd
        try:
            client = self._client()
            marks: dict[str, SessionMark] = {}
            for session in self._tree(client, session_id):
                listing = self._list(client, session.id, None, budget=None)
                messages = [parse_message(raw) for raw in listing.raw]
                opened = {message.id: _opened(message) for message in messages if is_open(message)}
                marks[session.id] = SessionMark(listing.cursor, messages[-1].id if messages else None, opened)
        except (_NotOursError, *_READ_FAULTS) as exc:
            self._errors.from_io_recovered(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return None
        return TranscriptPosition(TranscriptMark(marks).token)

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        """The serialized root conversation's size, its descendants excluded."""
        del spawn_cwd
        listed = self._root_messages(session_id)
        return None if listed is None else len(json.dumps(listed, separators=(",", ":")).encode())

    def context_tokens(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        """What the newest root step read: its input, cache reads and cache writes."""
        del spawn_cwd
        listed = self._root_messages(session_id)
        if listed is None:
            return None
        for raw in reversed(listed):
            if raw.get("type") != "assistant":
                continue
            try:
                tokens = parse_message(raw).tokens
            except OpenCodeTwoShapeError:
                continue
            if tokens is not None and tokens.output > 0:
                return tokens.input + tokens.cache_read + tokens.cache_write
        return None

    # -- reading ----------------------------------------------------------------------------------------------------

    def _client(self) -> OpenCodeServiceClient:
        registration = self._source()
        if registration is None:
            raise ServiceNotRegisteredError("no OpenCode service is registered")
        deadline = self._monotonic() + self._read_bound_seconds
        return OpenCodeServiceClient(registration, BoundedTransport(self._transport, deadline, self._monotonic))

    def _tree(self, client: OpenCodeServiceClient, root: str) -> list[_Session]:
        """``root`` and its descendants, breadth first; ``root`` must be a session this runner minted."""
        self._own(client, root)
        tree = [_Session(root, None)]
        frontier, depth = [root], 0
        while frontier and depth < MAX_TREE_DEPTH:
            found = [
                _Session(parse_session(child).id, parent) for parent in frontier for child in client.children(parent)
            ]
            known = {session.id for session in tree}
            found = [session for session in found if session.id not in known]
            tree.extend(found)
            frontier, depth = [session.id for session in found], depth + 1
        return tree

    def _list(
        self, client: OpenCodeServiceClient, session_id: str, cursor: str | None, *, budget: int | None
    ) -> _Listing:
        listing = _Listing(cursor=cursor)
        pages = 0
        page = (
            client.message_page(session_id, limit=MESSAGE_PAGE_LIMIT)
            if cursor is None
            else client.message_page(session_id, cursor=cursor, limit=MESSAGE_PAGE_LIMIT)
        )
        while True:
            pages += 1
            listing.raw.extend(page.items)
            if page.next is not None:
                listing.cursor = page.next
            # A short page is taken as the last, as the client's own listing takes it.
            if page.next is None or len(page.items) < MESSAGE_PAGE_LIMIT:
                return listing
            if budget is not None and pages >= budget:
                listing.exhausted = True
                return listing
            page = client.message_page(session_id, cursor=page.next, limit=MESSAGE_PAGE_LIMIT)

    def _read_session(self, client: OpenCodeServiceClient, session_id: str, mark: SessionMark) -> _SessionRead:
        """Open steps read again by id first, since they came earlier, then the messages after the cursor."""
        read = _SessionRead()
        opened: dict[str, OpenMessage] = {}
        for message_id, was in mark.open.items():
            try:
                message = parse_message(client.message(session_id, message_id))
            except ServiceRequestError as exc:
                if exc.status == 404:
                    continue
                raise
            made = message_turns(message, was.shipped)
            read.extend(made.turns, made.calls)
            patches, waiting = late_outputs(message, was.pending)
            read.late.extend(patches)
            _note_children(read, message)
            if is_open(message):
                opened[message.id] = OpenMessage(max(was.shipped, shippable(message)), (*waiting, *made.pending))
        listing = self._list(client, session_id, mark.cursor, budget=self._page_budget)
        last = mark.last
        for raw in listing.raw:
            message = parse_message(raw)
            last = message.id
            made = message_turns(message)
            read.extend(made.turns, made.calls)
            _note_children(read, message)
            if is_open(message):
                opened[message.id] = OpenMessage(shippable(message), made.pending)
        read.mark = SessionMark(listing.cursor, last, opened)
        read.exhausted = listing.exhausted
        return read

    def _root_messages(self, session_id: str) -> list[JsonObject] | None:
        try:
            client = self._client()
            self._own(client, session_id)
            return self._list(client, session_id, None, budget=None).raw
        except (_NotOursError, *_READ_FAULTS) as exc:
            self._errors.from_io_recovered(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return None

    def _own(self, client: OpenCodeServiceClient, session_id: str) -> None:
        try:
            record = parse_session(client.session(session_id))
        except ServiceRequestError as exc:
            if exc.status == 404:
                raise _NotOursError(session_id) from exc
            raise
        if not minted_by(record.metadata, self._runner_id):
            raise _NotOursError(session_id)

    # -- assembling -------------------------------------------------------------------------------------------------

    def _batch(
        self,
        root: str,
        tree: Sequence[_Session],
        reads: Mapping[str, _SessionRead],
        mark: TranscriptMark,
        version: str,
    ) -> TranscriptBatch:
        """Each descendant's new turns, deepest first, nested under its spawning call when that call is in this
        batch, and otherwise shipped apart under its session id for the pump to link."""
        spawned = {child: call for read in reads.values() for child, call in read.children.items()}
        agents = {child: agent for read in reads.values() for child, agent in read.agents.items()}
        unlinked: list[SidechainConversation] = []
        for session in reversed(tree[1:]):
            read = reads[session.id]
            if not read.turns:
                continue
            assert session.parent is not None  # every descendant has one
            parent = reads[session.parent]
            call = spawned.get(session.id)
            index = parent.calls.get(call) if call is not None else None
            sidechain = SidechainConversation(
                agent_id=session.id,
                agent_type=agents.get(session.id),
                link="session-id" if index is not None else "unlinked",
                turns=_indexed(read.turns),
            )
            if index is not None:
                parent.turns[index] = replace(parent.turns[index], sidechain=sidechain)
            else:
                unlinked.append(sidechain)
        marks = dict(mark.sessions)
        marks.update({session_id: read.mark for session_id, read in reads.items()})
        return TranscriptBatch(
            session_id=root,
            available=True,
            reason=None,
            turns=_indexed(reads[root].turns),
            unlinked_sidechains=list(reversed(unlinked)),
            next_position=TranscriptPosition(TranscriptMark(marks).token),
            complete=not any(read.exhausted for read in reads.values()),
            truncated=False,
            sidechain_truncated=False,
            normalizer_version=NORMALIZER_VERSION,
            harness_version=version,
            late_tool_outputs=[late for read in reads.values() for late in read.late],
            agent_tool_use_ids=spawned,
        )


def _note_children(read: _SessionRead, message: Message) -> None:
    read.children.update(spawned_children(message))
    for part in message.parts:
        if part.name != SUBAGENT_TOOL or part.state is None or part.state.child_session_id is None:
            continue
        agent = part.state.input.get("agent")
        if isinstance(agent, str) and agent:
            read.agents[part.state.child_session_id] = agent


def _opened(message: Message) -> OpenMessage:
    """An open step as a read through now would leave it: shipped as far as it can, its unfinished calls pending."""
    shipped = shippable(message)
    pending = tuple(
        part.id for part in message.parts[:shipped] if part.type == "tool" and part.id and tool_output(part) is None
    )
    return OpenMessage(shipped, pending)


def _between(
    listed: Sequence[JsonObject], start: SessionMark, end: TranscriptMark | None, session_id: str
) -> list[JsonObject]:
    """``listed`` after ``start``'s last message through ``end``'s; a session ``end`` never read is past it."""
    ids = [message.get("id") for message in listed]
    begin = ids.index(start.last) + 1 if start.last in ids else 0
    if end is None:
        return list(listed[begin:])
    if session_id not in end.sessions:
        return []
    stop_at = end.of(session_id).last
    stop = ids.index(stop_at) + 1 if stop_at in ids else 0
    return list(listed[begin:stop])


def _record(session: _Session, message: JsonObject) -> str:
    return json.dumps({"session": session.id, "parent": session.parent, "message": message}, sort_keys=True)


def _indexed(turns: Sequence[NormalizedTurn]) -> list[NormalizedTurn]:
    return [replace(turn, index=index) for index, turn in enumerate(turns)]


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


_harness_transcript_source_conforms: type[IHarnessTranscriptSource] = OpenCodeTwoTranscriptSource

__all__ = ["DEFAULT_PAGE_BUDGET", "DEFAULT_READ_BOUND_SECONDS", "MAX_TREE_DEPTH", "OpenCodeTwoTranscriptSource"]
