"""The Claude Code JSONL → :class:`NormalizedTurn` normalizer (blizzard#245).

Pure and stdlib-only (``bzh:domain-core``): :func:`normalize_lines` takes an
**iterable of strings** — already-read lines, never a path — which is what makes it
unit-testable with no filesystem; the file locate/read step (including sidecar
discovery and the file-size tail bound) lives in the sibling
``claude_code_transcript.py``, the only module in this pair that touches
``pathlib``/``glob``.

Skips ``isMeta``/control/system records, strips ANSI escapes, and collapses
``env``/``asst``/``tool`` records (an ``isSidechain`` record is routed to
inline-sidechain assembly, never dropped) — plus a ``thinking`` turn kind and
structured (never ``json.dumps``'d) tool-call input. ``MAX_TURNS`` deliberately does
not live here: a forward incremental read must never silently drop turns, so that
recency cap lives on the panel projection instead
(``transcripts/internal/projected_transcript_repository.py``).
``MAX_BLOCK_CHARS`` stays here — it caps every **string** block (assistant text,
thinking, tool output); a tool call's *input* is a mapping at this layer, not a
string, so there is no string here to cap; capping its serialized form moves to the
projection, the point at which it is re-materialized as one.

Records are read in **file order**, not DAG-traversed via ``uuid``/``parentUuid`` for
the *main* conversation — a fleet worker is ``claude -p`` (headless, ``--resume``
appends), so no rewind/branch is ever created there. The ``uuid``/``parentUuid`` chain
*is* consulted, but only for the narrower job of threading an inline sidechain run
together and resolving its root to a spawning tool call (route 2 below) — the
corpus-primary route, sidecar-file joining (route 1), is the sibling source module's
job, since it needs a second file this module never reads.

**Sidechain linking, in the order attempted, each recorded on the turn as
:data:`~blizzard.runner.harness.transcript.SidechainLink`:**

1. ``agent-id`` — resolved by ``claude_code_transcript.py``, not here; this module
   only surfaces the *candidates* (:attr:`NormalizedFile.agent_id_by_tool_turn`), one
   entry per tool turn whose matching ``tool_result`` record carried a
   ``toolUseResult.agentId``.
2. ``uuid-chain`` — an inline ``isSidechain`` run's root record's ``parentUuid``
   resolves to exactly one tool-call turn from the assistant record that ``uuid``
   names; an ambiguous match (that record emitted more than one tool call) falls
   through to route 3 rather than guess.
3. ``prompt-timestamp`` — the run's first user-role text matched against a
   candidate tool call's ``prompt``/``description`` input, nearest **preceding** call
   by timestamp winning among ties.
4. ``unlinked`` — carried on :class:`NormalizedFile`'s own unlinked list (mirroring
   :attr:`~blizzard.runner.harness.transcript.TranscriptBatch.unlinked_sidechains`,
   which the source assembles this module's per-file results into).
"""

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

#: The normalizer code version stamped onto every batch this module produces
#: (:attr:`~blizzard.runner.harness.transcript.TranscriptBatch.normalizer_version`).
#: Bumped when this module's output shape or semantics change, so a future better
#: normalizer's rows are told apart from this one's.
NORMALIZER_VERSION = "claude-code-jsonl/1"

#: Cap each text / thinking / tool-output string block at this many characters. A
#: tool call's *input* is a mapping at this layer, so there is no string here to cap;
#: that moves to the panel projection.
MAX_BLOCK_CHARS = 1024 * 1024

#: Control records: plumbing, never conversation. ``file-history-snapshot``/
#: ``file-history-delta``/``pr-link`` are already inert (they match neither the
#: ``assistant`` nor ``user`` branch below), but named here so their drop is
#: explicit rather than incidental.
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

#: Raw CSI ANSI escape sequences (e.g. `\x1b[31m`), including the private-mode `?`
#: prefix (`\x1b[?25l`) — a fleet worker shells out to interactive TUI tools that
#: emit these into its own transcript.
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class NormalizedFile:
    """:func:`normalize_lines`'s return — one JSONL file's records, normalized.

    Internal to the Claude Code adapter (``bzh:dependency-inversion`` — not part of
    the harness-agnostic seam in ``harness/transcript.py``): ``agent_id_by_tool_turn``
    is Claude-Code-specific plumbing the sibling source module needs to complete the
    sidecar-based agent-id join (link route 1) after discovering and normalizing a
    sidecar file of its own; nothing outside ``internal/`` needs to see it.

    ``frozen=True`` guards against rebinding a field on this instance — it does not
    make ``turns``/``unlinked_sidechains`` themselves immutable. The sibling source
    module treats both lists as an assembly buffer it finishes filling (completing
    the agent-id join by list-mutating ``turns[i]`` and appending late-resolved
    entries to ``unlinked_sidechains``) after :func:`normalize_lines` returns.
    """

    turns: list[NormalizedTurn]
    unlinked_sidechains: list[SidechainConversation]
    agent_id_by_tool_turn: dict[int, str]
    harness_version: str | None


def normalize_lines(lines: list[str], *, is_sidechain_file: bool = False) -> NormalizedFile:
    """Collapse one JSONL file's raw lines into :class:`NormalizedFile`.

    ``is_sidechain_file`` is set by a caller that already knows every record in
    ``lines`` belongs to one subagent's own conversation — a sidecar file
    (``<session-id>/subagents/agent-<id>.jsonl``), where every record carries
    ``isSidechain: true`` on itself but that flag no longer means "splice this
    elsewhere": it is simply what the whole file is. Left ``False`` (a top-level
    session file), an ``isSidechain`` record is routed to inline-sidechain assembly
    instead of the main turn stream — see the module docstring's route 2/3/4.

    An unrecognized or malformed record is skipped silently rather than raising — a
    third-party format change degrades to "fewer turns", never a crash.
    """
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

    turns, agent_id_by_tool_turn, tool_turns_by_record_uuid = _collapse(main_records)
    unlinked = _assemble_inline_sidechains(sidechain_records, turns, tool_turns_by_record_uuid)
    return NormalizedFile(
        turns=turns,
        unlinked_sidechains=unlinked,
        agent_id_by_tool_turn=agent_id_by_tool_turn,
        harness_version=harness_version,
    )


def _collapse(
    records: list[dict[str, Any]],
) -> tuple[list[NormalizedTurn], dict[int, str], dict[str, list[int]]]:
    pending_tool_index: dict[str, int] = {}
    tool_turns_by_record_uuid: dict[str, list[int]] = {}
    agent_id_by_tool_turn: dict[int, str] = {}
    turns: list[NormalizedTurn] = []

    for record in records:
        timestamp = _parse_timestamp(record.get("timestamp"))
        record_type = record.get("type")
        if record_type == "assistant":
            _collapse_assistant(record, timestamp, turns, pending_tool_index, tool_turns_by_record_uuid)
        elif record_type == "user":
            _collapse_user(record, timestamp, turns, pending_tool_index, agent_id_by_tool_turn)

    return turns, agent_id_by_tool_turn, tool_turns_by_record_uuid


def _collapse_assistant(
    record: dict[str, Any],
    timestamp: datetime | None,
    turns: list[NormalizedTurn],
    pending_tool_index: dict[str, int],
    tool_turns_by_record_uuid: dict[str, list[int]],
) -> None:
    content = record.get("message", {}).get("content") if isinstance(record.get("message"), dict) else None
    if not isinstance(content, list):
        return

    for block in content:
        if isinstance(block, dict) and block.get("type") == "thinking":
            turns.append(_thinking_turn(timestamp, block, len(turns)))

    text_parts = [str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"]
    joined = "\n".join(p for p in text_parts if p)
    if joined:
        text, block_truncated = _clean(joined)
        turns.append(_new_turn("asst", timestamp, len(turns), text=text, truncated=block_truncated))

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
        turns.append(
            NormalizedTurn(
                index=len(turns),
                kind="tool",
                timestamp=timestamp,
                text="",
                tool=tool,
                thinking_redacted=False,
                sidechain=None,
                truncated=False,
            )
        )
        tool_indices.append(len(turns) - 1)
        if tool_use_id is not None:
            pending_tool_index[tool_use_id] = len(turns) - 1

    if isinstance(record_uuid, str) and tool_indices:
        tool_turns_by_record_uuid.setdefault(record_uuid, []).extend(tool_indices)


def _collapse_user(
    record: dict[str, Any],
    timestamp: datetime | None,
    turns: list[NormalizedTurn],
    pending_tool_index: dict[str, int],
    agent_id_by_tool_turn: dict[int, str],
) -> None:
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None

    tool_result_blocks = None
    if isinstance(content, list):
        blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
        tool_result_blocks = blocks or None

    if tool_result_blocks is not None:
        tool_use_result = record.get("toolUseResult")
        agent_id = tool_use_result.get("agentId") if isinstance(tool_use_result, dict) else None
        # `toolUseResult.agentId` is one field on the record, not one per block — only
        # attributable to a specific tool turn when this record resolves exactly one
        # `tool_result`. A record carrying more than one would otherwise stamp every
        # one of those tool turns as the sidecar's spawning call, misattributing all
        # but (at most) the one that's actually true.
        agent_id_unambiguous = len(tool_result_blocks) == 1
        for block in tool_result_blocks:
            tool_use_id = block.get("tool_use_id")
            if not isinstance(tool_use_id, str):
                continue
            index = pending_tool_index.get(tool_use_id)
            if index is None:
                continue  # unmatched tool_result — dropped, as before
            output, output_truncated = _clean(_extract_text(block.get("content")))
            turn = turns[index]
            assert turn.tool is not None
            updated_tool = replace(
                turn.tool, output=output, output_truncated=turn.tool.output_truncated or output_truncated
            )
            turns[index] = replace(turn, tool=updated_tool, truncated=turn.truncated or output_truncated)
            if agent_id_unambiguous and isinstance(agent_id, str) and agent_id:
                agent_id_by_tool_turn[index] = agent_id
        return

    # A plain user record (first spawn prompt, or a later --resume injection) — env.
    text, block_truncated = _clean(_extract_text(content))
    turns.append(_new_turn("env", timestamp, len(turns), text=text, truncated=block_truncated))


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
    non-object value — see :class:`~blizzard.runner.harness.transcript.ToolCall`.
    The third element (:data:`~blizzard.runner.harness.transcript.ToolInputShape`)
    is the explicit discriminator a re-materializing consumer needs: ``input``/
    ``input_unparsed`` alone cannot tell "absent" from "an empty object", nor "a bare
    string" from "an already-serialized non-object" (a plain string that itself
    happens to parse as JSON is indistinguishable from the latter by inspection)."""
    if isinstance(raw_input, dict):
        return raw_input, None, "object"
    if raw_input is None:
        return {}, None, "absent"
    if isinstance(raw_input, str):
        return {}, raw_input, "string"
    return {}, json.dumps(raw_input), "other"


# --- inline sidechain assembly (link routes 2-4) -----------------------------


def _assemble_inline_sidechains(
    sidechain_records: list[dict[str, Any]],
    turns: list[NormalizedTurn],
    tool_turns_by_record_uuid: dict[str, list[int]],
) -> list[SidechainConversation]:
    if not sidechain_records:
        return []

    unlinked: list[SidechainConversation] = []
    # Every tool turn index a run has already claimed as its spawning call — checked
    # by every later run so two independent sidechains matching the same prompt (or,
    # in principle, colliding uuid-chain resolutions) never silently collapse to
    # whichever is processed last; the loser falls through to its own next route
    # instead, recorded nowhere is exactly the outcome this guards against.
    claimed: set[int] = set()
    # Built once, not per run: route 3 otherwise rescans every turn for every
    # sidechain run (O(runs x turns)), which compounds with a large main
    # conversation exactly when a large sidechain volume already makes the grouping
    # step expensive.
    tool_turn_indices_by_prompt = _index_tool_turns_by_prompt(turns)
    for run in _group_sidechain_runs(sidechain_records):
        # `run`'s records all carry `isSidechain: true` themselves (that's how they
        # were bucketed) — normalize them as their own main conversation, not fork
        # them into a further level of sidechain routing.
        conv_turns = _normalize_records(run, is_sidechain_file=True).turns
        agent_id = _first_str(run, "agentId")

        target_index = _resolve_uuid_chain(run, tool_turns_by_record_uuid)
        link: SidechainLink = "uuid-chain"
        if target_index is not None and target_index in claimed:
            target_index = None
        if target_index is None:
            target_index = _resolve_prompt_timestamp(run, turns, claimed, tool_turn_indices_by_prompt)
            link = "prompt-timestamp"
        if target_index is None:
            link = "unlinked"

        agent_type = _infer_agent_type(run, turns, target_index)
        conversation = SidechainConversation(agent_id=agent_id, agent_type=agent_type, link=link, turns=conv_turns)

        if target_index is None:
            unlinked.append(conversation)
        else:
            claimed.add(target_index)
            turns[target_index] = replace(turns[target_index], sidechain=conversation)

    return unlinked


def _group_sidechain_runs(records: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Thread inline ``isSidechain`` records into conversations by ``parentUuid``.

    A fleet worker's own conversation never forks (headless ``--resume`` only
    appends), and a subagent's is the same shape, so each run is a linear chain: a
    root (a record whose ``parentUuid`` does not name another sidechain record in
    this set) followed by the records that chain onto it, one link at a time. A
    record lacking usable ``uuid``/``parentUuid`` fields at all falls back to one
    shared run in file order rather than being silently dropped.

    Linear in ``len(records)`` regardless of well-formedness: a ``parentUuid`` index is
    built once up front as a queue per key, so finding each link's successor is an
    O(1) dequeue rather than a scan — a naive per-link rescan-with-filter is quadratic
    whenever many links share one lookup key, measured at 29.4s for 40,000 records
    with duplicate ``uuid`` values (a volume an in-spec ~50 MB session transcript can
    plausibly contain), which is the shape that actually triggers it: several
    *different* current-record ``uuid``\\ s colliding on the same string value, so
    their walks repeatedly probe the same key's child pool. A shared ``parentUuid``
    alone (several genuine children of one parent) does not degrade this: each is
    still dequeued once, in file order, first-unused-wins.
    """
    by_uuid = {r["uuid"]: r for r in records if isinstance(r.get("uuid"), str)}
    # Every record indexed by its own `parentUuid` as a queue, so walking a chain
    # forward from a root dequeues each candidate exactly once — no rescan of a shared
    # key's pool, which is what makes a `uuid` collision (two unrelated walks probing
    # the same key) cost the same as a clean one. More than one child sharing a
    # `parentUuid` is possible in principle (malformed or unusual data) — resolved in
    # file order, first-unused-wins, same as an unshared key.
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
                    # Drain any front entries already consumed elsewhere (possible only
                    # under a duplicate `uuid` — two unrelated walks probing the same
                    # key) — each entry is drained at most once ever, across the whole
                    # function, so this stays amortized O(1) per link rather than a
                    # rescan.
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


def _resolve_uuid_chain(run: list[dict[str, Any]], tool_turns_by_record_uuid: dict[str, list[int]]) -> int | None:
    parent_uuid = run[0].get("parentUuid")
    if not isinstance(parent_uuid, str):
        return None
    indices = tool_turns_by_record_uuid.get(parent_uuid)
    if not indices or len(indices) != 1:
        # No match, or the spawning record emitted more than one tool call —
        # ambiguous, so fall through to route 3 rather than guess.
        return None
    return indices[0]


def _index_tool_turns_by_prompt(turns: list[NormalizedTurn]) -> dict[str, list[int]]:
    """Every tool-call turn index, keyed by its own ``prompt``/``description`` input
    text (route 3's match key) — built once so :func:`_resolve_prompt_timestamp`
    never rescans every turn per sidechain run. Only a timestamped turn is indexed:
    an un-timestamped one can never win "nearest preceding" (see that function), so
    indexing it would only cost memory for a candidate that can never be returned.
    """
    index: dict[str, list[int]] = {}
    for i, turn in enumerate(turns):
        if turn.kind != "tool" or turn.tool is None or turn.timestamp is None:
            continue
        candidate = turn.tool.input.get("prompt") or turn.tool.input.get("description")
        if isinstance(candidate, str) and candidate:
            index.setdefault(candidate, []).append(i)
    return index


def _resolve_prompt_timestamp(
    run: list[dict[str, Any]],
    turns: list[NormalizedTurn],
    claimed: set[int],
    tool_turn_indices_by_prompt: dict[str, list[int]],
) -> int | None:
    prompt_text = _first_user_text(run)
    if not prompt_text:
        return None
    root_timestamp = _parse_timestamp(run[0].get("timestamp"))

    best_index: int | None = None
    best_timestamp: datetime | None = None
    for i in tool_turn_indices_by_prompt.get(prompt_text, ()):
        if i in claimed:
            continue
        turn = turns[i]
        assert turn.timestamp is not None  # only a timestamped turn is ever indexed
        if root_timestamp is not None and turn.timestamp > root_timestamp:
            continue  # only a preceding call can be this sidechain's spawn
        if best_timestamp is None or turn.timestamp > best_timestamp:
            best_index = i
            best_timestamp = turn.timestamp
    return best_index


def _infer_agent_type(run: list[dict[str, Any]], turns: list[NormalizedTurn], target_index: int | None) -> str | None:
    if target_index is not None:
        tool = turns[target_index].tool
        if tool is not None:
            candidate = tool.input.get("subagent_type")
            if isinstance(candidate, str):
                return candidate
    return _first_str(run, "agentType")


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
