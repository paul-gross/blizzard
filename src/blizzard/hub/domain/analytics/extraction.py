"""Per-kind turn recognition (blizzard#254, Phase 2) — pure over turn objects, no store
or sweep (``bzh:domain-core``).

An extractor recognizes calls per *dialect*, keyed on the segment's own
``normalizer_version`` (D9); an unknown dialect derives zero events rather than
guessing. Adding a kind is registering a new extractor (D5)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from blizzard.hub.domain.analytics.events import KIND_AGENT_SPAWN, KIND_FILE_READ, KIND_SKILL_INVOCATION
from blizzard.wire.transcript_segment import TurnSegmentView

#: Bumped when recognition changes — the sweep re-derives history, leaving earlier
#: rows untouched (D5/D9).
EXTRACTOR_VERSION = "blizzard-analytics/3"

#: The one dialect this build's extractors know (A1) — Claude Code's own normalizer
#: stamp, mapped to the tool name that dialect uses for an agent spawn (blizzard#327).
#: A future harness naming it differently is a new dialect entry here, not a rewrite.
_CLAUDE_CODE_DIALECTS: dict[str, str] = {"claude-code-jsonl/2": "Agent"}


@dataclass(frozen=True)
class ExtractedEvent:
    """One recognized occurrence, still payload-shaped as a plain mapping — the
    derivation service (Phase 3) serializes it and stamps the node-step context this
    layer never sees. ``subject``/``tool`` are the projection its extractor supplies
    (blizzard#255 D1); ``subject`` is ``None`` for a kind with no natural one."""

    kind: str
    turn_path: str
    occurrence: int
    payload: dict[str, object]
    subject: str | None
    tool: str | None
    depth: int
    agent_type: str | None
    occurred_at: datetime | None


class ITurnEventExtractor(Protocol):
    """One kind's recognizer. ``kind`` is a class-level constant. :meth:`recognize`
    returns every payload this turn mints, ``[]`` for none — including gating on which
    tool name this turn's own dialect uses, since that can vary by dialect (blizzard#327);
    :meth:`subject` reads that payload's own subject (blizzard#255 D1)."""

    kind: str

    def recognize(self, turn: TurnSegmentView, *, normalizer_version: str) -> list[dict[str, object]]: ...

    def subject(self, payload: dict[str, object]) -> str | None:
        """This kind's subject within one of :meth:`recognize`'s own payloads —
        ``None`` for a kind with no natural one."""
        ...


class FileReadExtractor:
    """A :class:`Read` call naming a concrete path it read (D5) — a pattern search
    (``Grep``/``Glob``) is a different act and is not one."""

    kind = KIND_FILE_READ

    def subject(self, payload: dict[str, object]) -> str | None:
        path = payload.get("path")
        return path if isinstance(path, str) else None

    def recognize(self, turn: TurnSegmentView, *, normalizer_version: str) -> list[dict[str, object]]:
        if normalizer_version not in _CLAUDE_CODE_DIALECTS:
            return []
        if turn.kind != "tool" or turn.tool is None or turn.tool.name != "Read":
            return []
        path = turn.tool.input.get("file_path")
        if not isinstance(path, str) or not path:
            return []
        return [{"tool_name": turn.tool.name, "path": path}]


class SkillInvocationExtractor:
    """A ``Skill`` call naming which skill it invoked."""

    kind = KIND_SKILL_INVOCATION

    def subject(self, payload: dict[str, object]) -> str | None:
        skill_name = payload.get("skill_name")
        return skill_name if isinstance(skill_name, str) else None

    def recognize(self, turn: TurnSegmentView, *, normalizer_version: str) -> list[dict[str, object]]:
        if normalizer_version not in _CLAUDE_CODE_DIALECTS:
            return []
        if turn.kind != "tool" or turn.tool is None or turn.tool.name != "Skill":
            return []
        skill_name = turn.tool.input.get("skill")
        if not isinstance(skill_name, str) or not skill_name:
            return []
        return [{"skill_name": skill_name}]


class AgentSpawnExtractor:
    """A subagent-spawn call naming the subagent type it spawned — which tool name that
    is comes from the turn's own dialect (``_CLAUDE_CODE_DIALECTS``), since it is not the
    same across every harness (blizzard#327)."""

    kind = KIND_AGENT_SPAWN

    def subject(self, payload: dict[str, object]) -> str | None:
        agent_type = payload.get("agent_type")
        return agent_type if isinstance(agent_type, str) else None

    def recognize(self, turn: TurnSegmentView, *, normalizer_version: str) -> list[dict[str, object]]:
        tool_name = _CLAUDE_CODE_DIALECTS.get(normalizer_version)
        if tool_name is None:
            return []
        if turn.kind != "tool" or turn.tool is None or turn.tool.name != tool_name:
            return []
        agent_type = turn.tool.input.get("subagent_type")
        if not isinstance(agent_type, str) or not agent_type:
            return []
        return [{"agent_type": agent_type}]


#: The registered set this build derives (D5) — appending a new extractor here is the
#: whole of "adding a kind."
DEFAULT_EXTRACTORS: tuple[ITurnEventExtractor, ...] = (
    FileReadExtractor(),
    SkillInvocationExtractor(),
    AgentSpawnExtractor(),
)


def _parse_occurred_at(raw: str | None) -> datetime | None:
    """An offset-less stamp is coerced to UTC rather than left naive
    (``bzh:utc-instants``); an unparseable or absent one is honestly untimed."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def extract_events(
    turns: list[TurnSegmentView],
    *,
    normalizer_version: str,
    extractors: Sequence[ITurnEventExtractor] = DEFAULT_EXTRACTORS,
) -> list[ExtractedEvent]:
    """Every extractor's recognized events across ``turns`` and every nested sidechain
    (D8): depth 0 at the main lane, incrementing once per sidechain nesting level;
    ``agent_type`` is the **nearest-enclosing** sidechain's own — never inherited past an
    unresolved link, which carries depth with no agent type rather than borrowing an
    ancestor's."""
    return _walk(
        turns, normalizer_version=normalizer_version, extractors=extractors, depth=0, agent_type=None, path_prefix=""
    )


def _walk(
    turns: list[TurnSegmentView],
    *,
    normalizer_version: str,
    extractors: Sequence[ITurnEventExtractor],
    depth: int,
    agent_type: str | None,
    path_prefix: str,
) -> list[ExtractedEvent]:
    events: list[ExtractedEvent] = []
    for i, turn in enumerate(turns):
        turn_path = str(i) if not path_prefix else f"{path_prefix}.{i}"
        for extractor in extractors:
            for occurrence, payload in enumerate(extractor.recognize(turn, normalizer_version=normalizer_version)):
                events.append(
                    ExtractedEvent(
                        kind=extractor.kind,
                        turn_path=turn_path,
                        occurrence=occurrence,
                        payload=payload,
                        subject=extractor.subject(payload),
                        tool=turn.tool.name if turn.tool is not None else None,
                        depth=depth,
                        agent_type=agent_type,
                        occurred_at=_parse_occurred_at(turn.timestamp),
                    )
                )
        if turn.sidechain is not None:
            events.extend(
                _walk(
                    turn.sidechain.turns,
                    normalizer_version=normalizer_version,
                    extractors=extractors,
                    depth=depth + 1,
                    agent_type=turn.sidechain.agent_type,
                    path_prefix=turn_path,
                )
            )
    return events
