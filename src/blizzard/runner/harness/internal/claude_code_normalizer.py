"""The Claude Code JSONL → :class:`NormalizedTurn` normalizer (blizzard#245).

Pure and stdlib-only (``bzh:domain-core``): :func:`normalize_lines` takes already-read
lines, never a path. Records are read in **file order** for the main conversation; the
``uuid``/``parentUuid`` chain is consulted only to thread an inline sidechain together.
Sidechain linking is tried ``agent-id``, ``uuid-chain``, ``prompt-timestamp``, unlinked."""

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
class NormalizedFile:
    """:func:`normalize_lines`'s return — one JSONL file's records, normalized.
    ``agent_id_by_tool_turn`` is *attachment* — an id resolved onto a specific tool turn;
    ``discovered_agent_ids`` is wider, every id these lines named at all. ``frozen=True``
    guards rebinding only: both lists stay mutable assembly buffers."""

    turns: list[NormalizedTurn]
    unlinked_sidechains: list[SidechainConversation]
    agent_id_by_tool_turn: dict[int, str]
    discovered_agent_ids: frozenset[str]
    harness_version: str | None


def normalize_lines(lines: list[str], *, is_sidechain_file: bool = False) -> NormalizedFile:
    """Collapse one JSONL file's raw lines into :class:`NormalizedFile`.

    ``is_sidechain_file`` marks a sidecar file whose every record is one subagent's own
    conversation, so ``isSidechain`` no longer means "splice this elsewhere". An
    unrecognized or malformed record is skipped silently — degrade, never crash."""
    records: list[dict[str, Any]] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return _normalize_records(records, is_sidechain_file=is_sidechain_file)


# --- record → turn collapse -------------------------------------------------


def _normalize_records(records: list[dict[str, Any]], *, is_sidechain_file: bool = False) -> NormalizedFile:
    harness_version: str | None = None
    main_records: list[dict[str, Any]] = []
    sidechain_records: list[dict[str, Any]] = []

    for record in records:
        version = record.get("version")
        if isinstance(version, str) and version:
            harness_version = version
        if record.get("isMeta"):
            continue
        if not is_sidechain_file and record.get("isSidechain"):
            sidechain_records.append(record)
            continue
        if record.get("type") in _CONTROL_TYPES:
            continue
        main_records.append(record)

    collapser = _TurnCollapser()
    collapser.feed_all(main_records)
    unlinked = (
        _SidechainAssembler(collapser.turns, collapser.tool_turns_by_record_uuid).assemble(sidechain_records)
        if sidechain_records
        else []
    )
    return NormalizedFile(
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

    def feed_all(self, records: list[dict[str, Any]]) -> None:
        for record in records:
            timestamp = _parse_timestamp(record.get("timestamp"))
            record_type = record.get("type")
            if record_type == "assistant":
                self._assistant(record, timestamp)
            elif record_type == "user":
                self._user(record, timestamp)

    def _assistant(self, record: dict[str, Any], timestamp: datetime | None) -> None:
        content = record.get("message", {}).get("content") if isinstance(record.get("message"), dict) else None
        if not isinstance(content, list):
            return

        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                self.turns.append(_thinking_turn(timestamp, block, len(self.turns)))

        text_parts = [str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"]
        joined = "\n".join(p for p in text_parts if p)
        if joined:
            text, block_truncated = _clean(joined)
            self.turns.append(_new_turn("asst", timestamp, len(self.turns), text=text, truncated=block_truncated))

        record_uuid = record.get("uuid")
        tool_indices: list[int] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = str(block.get("name", ""))
            input_map, input_unparsed, input_shape = _normalize_tool_input(block.get("input"))
            raw_tool_use_id = block.get("id")
            tool_use_id = raw_tool_use_id if isinstance(raw_tool_use_id, str) else None
            tool = ToolCall(
                name=name,
                input=input_map,
                input_unparsed=input_unparsed,
                input_shape=input_shape,
                tool_use_id=tool_use_id,
                output=None,
                output_truncated=False,
            )
            self.turns.append(
                NormalizedTurn(
                    index=len(self.turns),
                    kind="tool",
                    timestamp=timestamp,
                    text="",
                    tool=tool,
                    thinking_redacted=False,
                    sidechain=None,
                    truncated=False,
                )
            )
            tool_indices.append(len(self.turns) - 1)
            if tool_use_id is not None:
                self._pending_tool_index[tool_use_id] = len(self.turns) - 1

        if isinstance(record_uuid, str) and tool_indices:
            self.tool_turns_by_record_uuid.setdefault(record_uuid, []).extend(tool_indices)

    def _user(self, record: dict[str, Any], timestamp: datetime | None) -> None:
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None

        tool_result_blocks = None
        if isinstance(content, list):
            blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
            tool_result_blocks = blocks or None

        if tool_result_blocks is not None:
            tool_use_result = record.get("toolUseResult")
            raw_agent_id = tool_use_result.get("agentId") if isinstance(tool_use_result, dict) else None
            agent_id = raw_agent_id if isinstance(raw_agent_id, str) and raw_agent_id else None
            if agent_id is not None:
                # A discovered agent id is always a read *candidate*, whether or not it can
                # be attached below — attachment stays best-effort, discovery does not.
                self.discovered_agent_ids.add(agent_id)
            # `toolUseResult.agentId` is one field on the record, not one per block, so it is
            # attributable only when this record resolves exactly one `tool_result`.
            agent_id_unambiguous = len(tool_result_blocks) == 1
            for block in tool_result_blocks:
                tool_use_id = block.get("tool_use_id")
                if not isinstance(tool_use_id, str):
                    continue
                index = self._pending_tool_index.get(tool_use_id)
                if index is None:
                    continue  # unmatched tool_result — its tool_use fell outside this call's lines
                output, output_truncated = _clean(_extract_text(block.get("content")))
                turn = self.turns[index]
                assert turn.tool is not None
                updated_tool = replace(
                    turn.tool, output=output, output_truncated=turn.tool.output_truncated or output_truncated
                )
                self.turns[index] = replace(turn, tool=updated_tool, truncated=turn.truncated or output_truncated)
                if agent_id_unambiguous and agent_id is not None:
                    self.agent_id_by_tool_turn[index] = agent_id
            return

        # A plain user record (first spawn prompt, or a later --resume injection) — env.
        text, block_truncated = _clean(_extract_text(content))
        self.turns.append(_new_turn("env", timestamp, len(self.turns), text=text, truncated=block_truncated))


def _thinking_turn(timestamp: datetime | None, block: dict[str, Any], index: int) -> NormalizedTurn:
    raw_text = str(block.get("thinking", "") or "")
    # Thinking content is redacted universally (empty `thinking` + a `signature`) —
    # the expected shape, not an edge case, so this turn carries *presence*.
    redacted = not raw_text and bool(block.get("signature"))
    text, truncated = _clean(raw_text) if raw_text else ("", False)
    return NormalizedTurn(
        index=index,
        kind="thinking",
        timestamp=timestamp,
        text=text,
        tool=None,
        thinking_redacted=redacted,
        sidechain=None,
        truncated=truncated,
    )


def _new_turn(
    kind: NormalizedTurnKind, timestamp: datetime | None, index: int, *, text: str, truncated: bool
) -> NormalizedTurn:
    return NormalizedTurn(
        index=index,
        kind=kind,
        timestamp=timestamp,
        text=text,
        tool=None,
        thinking_redacted=False,
        sidechain=None,
        truncated=truncated,
    )


def _normalize_tool_input(raw_input: object) -> tuple[Mapping[str, Any], str | None, ToolInputShape]:
    """A tool call's ``input`` as a structured mapping, never coerced from a
    non-object value, plus the shape discriminator — see
    :class:`~blizzard.runner.harness.transcript.ToolCall` and
    :data:`~blizzard.runner.harness.transcript.ToolInputShape`."""
    if isinstance(raw_input, dict):
        return raw_input, None, "object"
    if raw_input is None:
        return {}, None, "absent"
    if isinstance(raw_input, str):
        return {}, raw_input, "string"
    return {}, json.dumps(raw_input), "other"


# --- inline sidechain assembly (link routes 2-4) -----------------------------


class _SidechainAssembler:
    """Inline sidechain runs spliced onto the tool turns that spawned them."""

    def __init__(self, turns: list[NormalizedTurn], tool_turns_by_record_uuid: dict[str, list[int]]) -> None:
        self.turns = turns
        self.tool_turns_by_record_uuid = tool_turns_by_record_uuid
        # Every tool turn index a run has already claimed, so two sidechains resolving to
        # the same call never collapse — the loser falls through to its own next route.
        self.claimed: set[int] = set()
        # Built once, not per run: route 3 otherwise rescans every turn per run.
        self.tool_turn_indices_by_prompt = _index_tool_turns_by_prompt(turns)

    def assemble(self, sidechain_records: list[dict[str, Any]]) -> list[SidechainConversation]:
        """Splice each run onto its spawning turn; return the runs that linked to none."""
        unlinked: list[SidechainConversation] = []
        for run in _group_sidechain_runs(sidechain_records):
            # `run`'s records all carry `isSidechain: true`, so normalize them as their own
            # main conversation rather than forking a further level of sidechain routing.
            conv_turns = _normalize_records(run, is_sidechain_file=True).turns
            agent_id = _first_str(run, "agentId")

            target_index = self._resolve_uuid_chain(run)
            link: SidechainLink = "uuid-chain"
            if target_index is not None and target_index in self.claimed:
                target_index = None
            if target_index is None:
                target_index = self._resolve_prompt_timestamp(run)
                link = "prompt-timestamp"
            if target_index is None:
                link = "unlinked"

            agent_type = self._infer_agent_type(run, target_index)
            conversation = SidechainConversation(agent_id=agent_id, agent_type=agent_type, link=link, turns=conv_turns)

            if target_index is None:
                unlinked.append(conversation)
            else:
                self.claimed.add(target_index)
                self.turns[target_index] = replace(self.turns[target_index], sidechain=conversation)

        return unlinked

    def _resolve_uuid_chain(self, run: list[dict[str, Any]]) -> int | None:
        parent_uuid = run[0].get("parentUuid")
        if not isinstance(parent_uuid, str):
            return None
        indices = self.tool_turns_by_record_uuid.get(parent_uuid)
        if not indices or len(indices) != 1:
            # No match, or the spawning record emitted more than one tool call —
            # ambiguous, so fall through to route 3 rather than guess.
            return None
        return indices[0]

    def _resolve_prompt_timestamp(self, run: list[dict[str, Any]]) -> int | None:
        prompt_text = _first_user_text(run)
        if not prompt_text:
            return None
        root_timestamp = _parse_timestamp(run[0].get("timestamp"))

        best_index: int | None = None
        best_timestamp: datetime | None = None
        for i in self.tool_turn_indices_by_prompt.get(prompt_text, ()):
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

    def _infer_agent_type(self, run: list[dict[str, Any]], target_index: int | None) -> str | None:
        if target_index is not None:
            tool = self.turns[target_index].tool
            if tool is not None:
                candidate = tool.input.get("subagent_type")
                if isinstance(candidate, str):
                    return candidate
        return _first_str(run, "agentType")


def _group_sidechain_runs(records: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Thread inline ``isSidechain`` records into conversations by ``parentUuid``.

    Each run is a linear chain from a root; a record lacking usable ``uuid``/``parentUuid``
    falls back to one shared run in file order rather than being dropped. Linear in
    ``len(records)`` (pinned by ``test_group_sidechain_runs_stays_fast_and_correct_under_duplicate_uuid_values``)."""
    by_uuid = {r["uuid"]: r for r in records if isinstance(r.get("uuid"), str)}
    # Indexed by `parentUuid` as a queue, so walking a chain forward dequeues each
    # candidate exactly once; shared parents resolve in file order, first-unused-wins.
    children_by_parent_uuid: dict[str, deque[dict[str, Any]]] = {}
    for record in records:
        parent_uuid = record.get("parentUuid")
        if isinstance(parent_uuid, str):
            children_by_parent_uuid.setdefault(parent_uuid, deque()).append(record)

    # A root is a record whose `parentUuid` does not name another record in this
    # sidechain set — either it has none, or it points out to the main conversation.
    roots = [r for r in records if not (isinstance(r.get("parentUuid"), str) and r.get("parentUuid") in by_uuid)]
    if not roots:
        return [records]

    runs: list[list[dict[str, Any]]] = []
    used: set[int] = set()
    for root in roots:
        if id(root) in used:
            continue
        run = [root]
        used.add(id(root))
        current = root
        while True:
            current_uuid = current.get("uuid")
            nxt = None
            if isinstance(current_uuid, str):
                queue = children_by_parent_uuid.get(current_uuid)
                if queue is not None:
                    # Drain entries already consumed elsewhere (only under a duplicate
                    # `uuid`); each drains at most once, so this stays amortized O(1).
                    while queue and id(queue[0]) in used:
                        queue.popleft()
                    if queue:
                        nxt = queue.popleft()
            if nxt is None:
                break
            run.append(nxt)
            used.add(id(nxt))
            current = nxt
        runs.append(run)

    leftover = [r for r in records if id(r) not in used]
    if leftover:
        runs.append(leftover)
    return runs


def _index_tool_turns_by_prompt(turns: list[NormalizedTurn]) -> dict[str, list[int]]:
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


def _first_user_text(run: list[dict[str, Any]]) -> str | None:
    for record in run:
        if record.get("type") != "user":
            continue
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        text = _extract_text(content)
        if text:
            return text
    return None


def _first_str(records: list[dict[str, Any]], key: str) -> str | None:
    for record in records:
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


# --- plumbing ----------------------------------------------------------------


def _extract_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return ""


def _parse_timestamp(raw: object) -> datetime | None:
    """Every returned datetime is timezone-aware — an offset-less stamp (never
    observed from Claude Code, but not excluded by ``fromisoformat`` either) is
    coerced to UTC rather than left naive, so every comparison this module makes
    between two parsed timestamps stays total (``bzh:utc-instants``): a naive/aware
    mismatch raises ``TypeError`` on the very first ``>`` route 3 makes."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _clean(text: str) -> tuple[str, bool]:
    """Strip ANSI escapes, then cap at :data:`MAX_BLOCK_CHARS` — returns ``(text, truncated)``."""
    stripped = _ANSI_RE.sub("", text)
    if len(stripped) > MAX_BLOCK_CHARS:
        return stripped[:MAX_BLOCK_CHARS], True
    return stripped, False
