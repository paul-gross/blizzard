"""The Claude Code JSONL → :class:`NormalizedTurn` normalizer (blizzard#245).

Pure and stdlib-only (``bzh:domain-core``): :meth:`NormalizedFile.of_lines` takes already-read
lines, never a path. Main-conversation records are read in **file order**; the ``uuid``/``parentUuid``
chain is consulted only to thread an inline sidechain, linked ``agent-id``, ``uuid-chain``,
``prompt-timestamp``, or unlinked."""

from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from blizzard.runner.harness.transcript import (
    NormalizedTurn,
    NormalizedTurnKind,
    SidechainConversation,
    SidechainLink,
    ToolCall,
    ToolInputShape,
)

#: The normalizer version stamped onto every batch; bumped when this module's output changes.
NORMALIZER_VERSION = "claude-code-jsonl/1"

#: Cap each text / thinking / tool-output string block at this many characters.
MAX_BLOCK_CHARS = 1024 * 1024

#: Control records: plumbing, never conversation — named so each drop is explicit.
_CONTROL_TYPES = frozenset(
    {
        "mode",
        "permission-mode",
        "last-prompt",
        "ai-title",
        "queue-operation",
        "system",
        "attachment",
        "file-history-snapshot",
        "file-history-delta",
        "pr-link",
    }
)

#: Raw CSI ANSI escape sequences, including the private-mode `?` prefix (`\x1b[?25l`).
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class Text:
    """One string block, ANSI-stripped and capped at :data:`MAX_BLOCK_CHARS`."""

    text: str
    truncated: bool

    @classmethod
    def of(cls, raw: str) -> Text:
        stripped = _ANSI_RE.sub("", raw)
        if len(stripped) > MAX_BLOCK_CHARS:
            return cls(stripped[:MAX_BLOCK_CHARS], True)
        return cls(stripped, False)

    @classmethod
    def of_content(cls, content: object) -> Text:
        return cls.of(cls.joined(content))

    @staticmethod
    def joined(content: object) -> str:
        """A content value's text, uncleaned: the string itself, or its ``text`` blocks."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"]
            return "\n".join(p for p in parts if p)
        return ""


_EMPTY = Text("", False)


@dataclass(frozen=True, eq=False)
class Record:
    """One raw JSONL record. Identity-bearing (``eq=False``): two records of identical content
    stay two records, which is what threads a run correctly under duplicate ``uuid`` values."""

    raw: dict[str, Any]

    @classmethod
    def parse(cls, lines: list[str]) -> list[Record]:
        """Every line that decodes to a JSON object; anything else is skipped — degrade, never crash."""
        records: list[Record] = []
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                records.append(cls(decoded))
        return records

    def field(self, key: str) -> str | None:
        """``key``'s value, when it holds a non-empty string."""
        value = self.raw.get(key)
        return value if isinstance(value, str) and value else None

    def blocks(self, kind: str) -> list[dict[str, Any]]:
        content = self.content
        if not isinstance(content, list):
            return []
        return [b for b in content if isinstance(b, dict) and b.get("type") == kind]

    @property
    def type(self) -> object:
        return self.raw.get("type")

    @property
    def version(self) -> str | None:
        return self.field("version")

    @property
    def is_meta(self) -> bool:
        return bool(self.raw.get("isMeta"))

    @property
    def is_sidechain(self) -> bool:
        return bool(self.raw.get("isSidechain"))

    @property
    def is_control(self) -> bool:
        return self.type in _CONTROL_TYPES

    @property
    def uuid(self) -> str | None:
        value = self.raw.get("uuid")
        return value if isinstance(value, str) else None

    @property
    def parent_uuid(self) -> str | None:
        value = self.raw.get("parentUuid")
        return value if isinstance(value, str) else None

    @property
    def at(self) -> datetime | None:
        """Timezone-aware or ``None`` — an offset-less stamp is coerced to UTC rather than left
        naive (``bzh:utc-instants``), so every comparison between two parsed stamps stays total."""
        raw = self.raw.get("timestamp")
        if not isinstance(raw, str) or not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    @property
    def content(self) -> object:
        message = self.raw.get("message")
        return message.get("content") if isinstance(message, dict) else None

    @property
    def text(self) -> str:
        return Text.joined(self.content)

    @property
    def result_agent_id(self) -> str | None:
        result = self.raw.get("toolUseResult")
        value = result.get("agentId") if isinstance(result, dict) else None
        return value if isinstance(value, str) and value else None


@dataclass(frozen=True, eq=False)
class Run:
    """One inline sidechain conversation: a linear ``parentUuid`` chain of records."""

    records: list[Record]

    @classmethod
    def thread(cls, records: list[Record]) -> list[Run]:
        """Thread inline ``isSidechain`` records into runs by ``parentUuid``; one lacking a usable
        ``uuid``/``parentUuid`` joins a shared trailing run rather than being dropped. Linear in
        ``len(records)`` (pinned by ``test_threading_stays_fast_under_duplicate_uuid_values``)."""
        by_uuid = {r.uuid: r for r in records if r.uuid is not None}
        # Indexed by `parentUuid` as a queue, so walking a chain forward dequeues each
        # candidate exactly once; shared parents resolve in file order, first-unused-wins.
        children: dict[str, deque[Record]] = {}
        for record in records:
            if record.parent_uuid is not None:
                children.setdefault(record.parent_uuid, deque()).append(record)

        # A root is a record whose `parentUuid` does not name another record in this
        # sidechain set — either it has none, or it points out to the main conversation.
        roots = [r for r in records if not (r.parent_uuid is not None and r.parent_uuid in by_uuid)]
        if not roots:
            return [cls(records)]

        runs: list[Run] = []
        used: set[Record] = set()
        for root in roots:
            if root in used:
                continue
            chain = [root]
            used.add(root)
            current = root
            while True:
                nxt = None
                queue = children.get(current.uuid) if current.uuid is not None else None
                if queue is not None:
                    # Drain entries already consumed elsewhere (only under a duplicate
                    # `uuid`); each drains at most once, so this stays amortized O(1).
                    while queue and queue[0] in used:
                        queue.popleft()
                    if queue:
                        nxt = queue.popleft()
                if nxt is None:
                    break
                chain.append(nxt)
                used.add(nxt)
                current = nxt
            runs.append(cls(chain))

        leftover = [r for r in records if r not in used]
        if leftover:
            runs.append(cls(leftover))
        return runs

    @property
    def agent_id(self) -> str | None:
        return self._first("agentId")

    @property
    def agent_type(self) -> str | None:
        return self._first("agentType")

    @property
    def parent_uuid(self) -> str | None:
        return self.records[0].parent_uuid

    @property
    def at(self) -> datetime | None:
        return self.records[0].at

    @property
    def prompt(self) -> str | None:
        """The first non-empty user text — route 3's join key."""
        for record in self.records:
            if record.type == "user" and record.text:
                return record.text
        return None

    def _first(self, key: str) -> str | None:
        for record in self.records:
            value = record.field(key)
            if value is not None:
                return value
        return None


@dataclass(frozen=True)
class ToolInput:
    """A tool call's ``input``, never coerced from a non-object value — see
    :class:`~blizzard.runner.harness.transcript.ToolCall` and
    :data:`~blizzard.runner.harness.transcript.ToolInputShape`."""

    mapping: Mapping[str, Any]
    unparsed: str | None
    shape: ToolInputShape

    @classmethod
    def of(cls, raw: object) -> ToolInput:
        if isinstance(raw, dict):
            return cls(raw, None, "object")
        if raw is None:
            return cls({}, None, "absent")
        if isinstance(raw, str):
            return cls({}, raw, "string")
        return cls({}, json.dumps(raw), "other")


@dataclass(frozen=True)
class NormalizedFile:
    """One JSONL file's records, normalized. ``agent_id_by_tool_turn`` is *attachment* — an id
    resolved onto a specific tool turn; ``discovered_agent_ids`` is wider, every id these lines
    named at all. ``frozen=True`` guards rebinding only: both lists stay mutable buffers."""

    turns: list[NormalizedTurn]
    unlinked_sidechains: list[SidechainConversation]
    agent_id_by_tool_turn: dict[int, str]
    discovered_agent_ids: frozenset[str]
    harness_version: str | None

    @classmethod
    def of_lines(cls, lines: list[str], *, is_sidechain_file: bool = False) -> NormalizedFile:
        """One JSONL file's raw lines, collapsed."""
        return cls.of(Record.parse(lines), is_sidechain_file=is_sidechain_file)

    @classmethod
    def of(cls, records: list[Record], *, is_sidechain_file: bool = False) -> NormalizedFile:
        """``is_sidechain_file`` marks a sidecar file whose every record is one subagent's own
        conversation, so ``isSidechain`` no longer means "splice this elsewhere"."""
        harness_version: str | None = None
        main: list[Record] = []
        sidechain: list[Record] = []

        for record in records:
            harness_version = record.version or harness_version
            if record.is_meta:
                continue
            if not is_sidechain_file and record.is_sidechain:
                sidechain.append(record)
                continue
            if record.is_control:
                continue
            main.append(record)

        collapser = _TurnCollapser()
        collapser.feed_all(main)
        unlinked = _SidechainAssembler(collapser).assemble(sidechain) if sidechain else []
        return cls(
            turns=collapser.turns,
            unlinked_sidechains=unlinked,
            agent_id_by_tool_turn=collapser.agent_id_by_tool_turn,
            discovered_agent_ids=frozenset(collapser.discovered_agent_ids),
            harness_version=harness_version,
        )


class _TurnCollapser:
    """The main conversation's records folded into turns, in file order."""

    def __init__(self) -> None:
        self.turns: list[NormalizedTurn] = []
        self.tool_turns_by_record_uuid: dict[str, list[int]] = {}
        self.agent_id_by_tool_turn: dict[int, str] = {}
        self.discovered_agent_ids: set[str] = set()
        #: `tool_use_id` → turn index, so a later `tool_result` lands its output.
        self._pending_tool_index: dict[str, int] = {}

    def feed_all(self, records: list[Record]) -> None:
        for record in records:
            if record.type == "assistant":
                self._assistant(record)
            elif record.type == "user":
                self._user(record)

    def _append(
        self,
        kind: NormalizedTurnKind,
        at: datetime | None,
        *,
        text: Text = _EMPTY,
        tool: ToolCall | None = None,
        thinking_redacted: bool = False,
    ) -> int:
        self.turns.append(
            NormalizedTurn(
                index=len(self.turns),
                kind=kind,
                timestamp=at,
                text=text.text,
                tool=tool,
                thinking_redacted=thinking_redacted,
                sidechain=None,
                truncated=text.truncated,
            )
        )
        return len(self.turns) - 1

    def _assistant(self, record: Record) -> None:
        at = record.at
        for block in record.blocks("thinking"):
            self._thinking(block, at)

        joined = Text.joined(record.blocks("text"))
        if joined:
            self._append("asst", at, text=Text.of(joined))

        tool_indices: list[int] = []
        for block in record.blocks("tool_use"):
            tool = self._tool_call(block)
            index = self._append("tool", at, tool=tool)
            tool_indices.append(index)
            if tool.tool_use_id is not None:
                self._pending_tool_index[tool.tool_use_id] = index

        if record.uuid is not None and tool_indices:
            self.tool_turns_by_record_uuid.setdefault(record.uuid, []).extend(tool_indices)

    def _thinking(self, block: dict[str, Any], at: datetime | None) -> None:
        raw = str(block.get("thinking", "") or "")
        # Thinking content is redacted universally (empty `thinking` + a `signature`) —
        # the expected shape, not an edge case, so this turn carries *presence*.
        redacted = not raw and bool(block.get("signature"))
        self._append("thinking", at, text=Text.of(raw) if raw else _EMPTY, thinking_redacted=redacted)

    @staticmethod
    def _tool_call(block: dict[str, Any]) -> ToolCall:
        arguments = ToolInput.of(block.get("input"))
        tool_use_id = block.get("id")
        return ToolCall(
            name=str(block.get("name", "")),
            input=arguments.mapping,
            input_unparsed=arguments.unparsed,
            input_shape=arguments.shape,
            tool_use_id=tool_use_id if isinstance(tool_use_id, str) else None,
            output=None,
            output_truncated=False,
            input_truncated=False,
        )

    def _user(self, record: Record) -> None:
        results = record.blocks("tool_result")
        if not results:
            # A plain user record (first spawn prompt, or a later --resume injection) — env.
            self._append("env", record.at, text=Text.of_content(record.content))
            return

        agent_id = record.result_agent_id
        if agent_id is not None:
            # A discovered agent id is always a read *candidate*, whether or not it can
            # be attached below — attachment stays best-effort, discovery does not.
            self.discovered_agent_ids.add(agent_id)
        # `toolUseResult.agentId` is one field on the record, not one per block, so it is
        # attributable only when this record resolves exactly one `tool_result`.
        unambiguous = len(results) == 1
        for block in results:
            tool_use_id = block.get("tool_use_id")
            if not isinstance(tool_use_id, str):
                continue
            index = self._pending_tool_index.get(tool_use_id)
            if index is None:
                continue  # unmatched tool_result — its tool_use fell outside this call's lines
            output = Text.of_content(block.get("content"))
            turn = self.turns[index]
            assert turn.tool is not None
            updated_tool = replace(
                turn.tool, output=output.text, output_truncated=turn.tool.output_truncated or output.truncated
            )
            self.turns[index] = replace(turn, tool=updated_tool, truncated=turn.truncated or output.truncated)
            if unambiguous and agent_id is not None:
                self.agent_id_by_tool_turn[index] = agent_id


class _SidechainAssembler:
    """Inline sidechain runs spliced onto the tool turns that spawned them."""

    def __init__(self, collapser: _TurnCollapser) -> None:
        self.turns = collapser.turns
        self.tool_turns_by_record_uuid = collapser.tool_turns_by_record_uuid
        # Every tool turn index a run has already claimed, so two sidechains resolving to
        # the same call never collapse — the loser falls through to its own next route.
        self.claimed: set[int] = set()
        # Built once, not per run: route 3 otherwise rescans every turn per run.
        self.by_prompt = self._index_by_prompt(self.turns)

    def assemble(self, records: list[Record]) -> list[SidechainConversation]:
        """Splice each run onto its spawning turn; return the runs that linked to none."""
        unlinked: list[SidechainConversation] = []
        for run in Run.thread(records):
            # A run's records all carry `isSidechain: true`, so normalize them as their own
            # main conversation rather than forking a further level of sidechain routing.
            conv_turns = NormalizedFile.of(run.records, is_sidechain_file=True).turns

            target_index = self._resolve_uuid_chain(run)
            link: SidechainLink = "uuid-chain"
            if target_index is not None and target_index in self.claimed:
                target_index = None
            if target_index is None:
                target_index = self._resolve_prompt_timestamp(run)
                link = "prompt-timestamp"
            if target_index is None:
                link = "unlinked"

            conversation = SidechainConversation(
                agent_id=run.agent_id,
                agent_type=self._infer_agent_type(run, target_index),
                link=link,
                turns=conv_turns,
            )

            if target_index is None:
                unlinked.append(conversation)
            else:
                self.claimed.add(target_index)
                self.turns[target_index] = replace(self.turns[target_index], sidechain=conversation)

        return unlinked

    @staticmethod
    def _index_by_prompt(turns: list[NormalizedTurn]) -> dict[str, list[int]]:
        """Every tool-call turn index, keyed by its own ``prompt``/``description`` input text.
        Only a timestamped turn is indexed: an un-timestamped one can never win "nearest
        preceding"."""
        index: dict[str, list[int]] = {}
        for i, turn in enumerate(turns):
            if turn.kind != "tool" or turn.tool is None or turn.timestamp is None:
                continue
            candidate = turn.tool.input.get("prompt") or turn.tool.input.get("description")
            if isinstance(candidate, str) and candidate:
                index.setdefault(candidate, []).append(i)
        return index

    def _resolve_uuid_chain(self, run: Run) -> int | None:
        if run.parent_uuid is None:
            return None
        indices = self.tool_turns_by_record_uuid.get(run.parent_uuid)
        if not indices or len(indices) != 1:
            # No match, or the spawning record emitted more than one tool call —
            # ambiguous, so fall through to route 3 rather than guess.
            return None
        return indices[0]

    def _resolve_prompt_timestamp(self, run: Run) -> int | None:
        prompt = run.prompt
        if not prompt:
            return None
        root_timestamp = run.at

        best_index: int | None = None
        best_timestamp: datetime | None = None
        for i in self.by_prompt.get(prompt, ()):
            if i in self.claimed:
                continue
            turn = self.turns[i]
            assert turn.timestamp is not None  # only a timestamped turn is ever indexed
            if root_timestamp is not None and turn.timestamp > root_timestamp:
                continue  # only a preceding call can be this sidechain's spawn
            if best_timestamp is None or turn.timestamp > best_timestamp:
                best_index = i
                best_timestamp = turn.timestamp
        return best_index

    def _infer_agent_type(self, run: Run, target_index: int | None) -> str | None:
        if target_index is not None:
            tool = self.turns[target_index].tool
            if tool is not None:
                candidate = tool.input.get("subagent_type")
                if isinstance(candidate, str):
                    return candidate
        return run.agent_type
