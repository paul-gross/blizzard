"""Transcript domain types and the read-only repository seam.

:class:`Turn`/:class:`Transcript` are the parsed read model, carrying the hub segment wire's own turn
shape — thinking turns and sidechains included. A missing or unreadable transcript
is a **normal** outcome, not an exception: ``.available``/``.reason`` carry it in-band. Read-only by
design (``bzh:repository-split``) — the separate outbound lane does the writing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.foundation.transcripts import TranscriptUnavailable, TurnKind


@domain_model
@dataclass(frozen=True)
class ToolCall:
    """A tool invocation, structured — mirrors
    :class:`~blizzard.runner.harness.transcript.ToolCall`. Carried through, never
    re-materialized to a JSON string — rendering structured ``input``
    is the viewer's job."""

    name: str
    input: Mapping[str, object]
    input_unparsed: str | None
    input_shape: str
    tool_use_id: str | None
    output: str | None
    output_truncated: bool
    #: A late-arriving ``output`` for the same ``tool_use_id``; the reader folds and drops this turn.
    output_patch: bool = False


@domain_model
@dataclass(frozen=True)
class Sidechain:
    """A subagent's private conversation, nested under its spawning tool turn (or, when
    ``link == "unlinked"``, carried as its own top-level ``"sidechain"`` turn instead) —
    mirrors :class:`~blizzard.runner.harness.transcript.SidechainConversation`. Recursive:
    one of ``turns`` may itself carry a tool call whose own sidechain nests further."""

    agent_id: str | None
    agent_type: str | None
    link: str
    turns: list[Turn]
    #: The spawning call's ``tool_use_id`` when it shipped earlier — the handle a reader nests under.
    parent_tool_use_id: str | None = None


@domain_model
@dataclass(frozen=True)
class Turn:
    """One conversation turn, carried in full. ``tool``/``sidechain`` populate only
    on a ``kind="tool"`` turn, except a ``"sidechain"`` turn's own ``sidechain``, which stands alone
    (unlinked); ``thinking_redacted`` is ``kind="thinking"``-only. ``tool.output`` is ``None`` while
    pending; ``truncated`` is block-level, distinct from :attr:`Transcript.truncated`."""

    index: int
    kind: TurnKind
    timestamp: datetime | None
    text: str
    tool: ToolCall | None
    thinking_redacted: bool
    sidechain: Sidechain | None
    truncated: bool


#: Keep only the most recent this-many top-level turns — one cap for both the local and the archived read.
MAX_TURNS = 1000


def recent_window[T](turns: Sequence[T], *, max_turns: int = MAX_TURNS) -> tuple[list[T], bool]:
    """The newest ``max_turns`` of ``turns``, and whether that cut any."""
    capped = len(turns) > max_turns
    return list(turns[-max_turns:] if capped else turns), capped


@domain_model
@dataclass(frozen=True)
class Transcript:
    """A lease's parsed session — the transcript read model. ``available=False`` carries
    ``reason`` and an empty ``turns``, so a caller must check it before reading ``turns``.
    ``truncated`` is file-level: the tail-byte cap, ``MAX_TURNS``, or a sidechain-only read
    budget cut content the panel renders, distinct from a turn's own :attr:`Turn.truncated`."""

    session_id: str | None
    available: bool
    reason: TranscriptUnavailable | None
    turns: list[Turn]
    truncated: bool

    @classmethod
    def spawning(cls) -> Transcript:
        """A lease minted but not yet spawned: no session on either side yet — ordinary, not an error."""
        return cls(session_id=None, available=False, reason="spawning", turns=[], truncated=False)


class IReadTranscriptRepository(Protocol):
    """The transcript lookup seam. Read-only (``bzh:repository-split``).

    One operation. Raw-lines and size-on-disk reads are a separate concern, reached off
    the harness transcript source directly rather than through this seam."""

    def read_turns(self, session_id: str, *, spawn_cwd: str | None, since: str | None = None) -> Transcript:
        """The session's parsed transcript, located by ``session_id`` alone.

        ``spawn_cwd`` disambiguates when several project directories hold a same-id file,
        else ``None``. ``since`` is a forward-read cursor (``TranscriptSegmentState.cursor``)
        bounding the read to what followed it; ``None`` reads from the start."""
        ...


class ITranscriptRepositoryResolver(Protocol):
    """Resolves one recorded owner's :class:`IReadTranscriptRepository`, by its exact
    harness id (``bzh:dependency-inversion``) — so a caller holding a session never needs
    anything wider than that one owner's own repository."""

    def transcript_repository(self, harness_id: str) -> IReadTranscriptRepository:
        """The owner's read repository, or raise ``UnknownHarnessError`` /
        ``UnavailableHarnessError`` (``blizzard.runner.harness.registry``) — never a
        substitute owner's."""
        ...
