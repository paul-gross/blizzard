"""The Claude Code ``IHarnessTranscriptSource`` adapter (blizzard#245).

The **only** module in this pair that touches ``pathlib``/``glob`` I/O
(``bzh:dependency-inversion``); confined to ``internal/``. Absorbs
``transcripts/locator.py`` (``mangle_cwd``) and ``transcripts/internal/
jsonl_transcript_repository.py`` (session-id glob, multi-match disambiguation,
tail-seek read) — deliberately re-declared here rather than imported, mirroring
:class:`~blizzard.runner.harness.transcript.TranscriptErrorFactory`'s own
same-shape duplication (``harness/transcript.py``'s docstring): phases 1-3 add
code nothing calls yet, so the old modules keep every existing caller working
untouched until phase 4 deletes them and this module's owns the knowledge alone.

Adds what ``transcripts/`` never needed: sidecar-file discovery (the corpus-primary
sidechain shape, ``harness/internal/claude_code_normalizer.py``'s module docstring
finding 1) under ``<project-dir>/<session-id>/subagents/agent-<agentId>.jsonl``, the
agent-id join (link route 1) against
:attr:`~blizzard.runner.harness.internal.claude_code_normalizer.NormalizedFile.
agent_id_by_tool_turn`'s candidates, and :class:`~blizzard.runner.harness.transcript.
TranscriptPosition` minting/reading for forward incremental reads.

**Read shape, per the plan's cap-reconciliation table** (``harness/internal/
claude_code_normalizer.py``'s sibling, the *values* module's own docstring):

* ``since=None`` — a cold read. Each file involved (the main session file, and any
  sidecar discovered from a candidate spawn) is seeked to its own tail — the last
  :data:`MAX_FILE_BYTES` — and read to EOF uncapped by :data:`MAX_BATCH_BYTES`. A
  tail seek can land mid-line/mid-codepoint, so the first split fragment is
  discarded (``errors="replace"`` covers a split UTF-8 codepoint at the boundary).
  ``TranscriptBatch.truncated`` reflects only this cap, and only on this path.
* ``since=<position>`` — a forward read starting at that exact byte offset (always a
  newline boundary, by construction — see below), bounded by one shared
  :data:`MAX_BATCH_BYTES` budget spent across the main file first, then whatever
  sidecars a route-1 candidate names, in that order; a sidecar the budget never
  reaches keeps its ``since`` offset unchanged in the minted ``next_position``, and
  the batch reports ``complete=False``. A read that lands mid-line (the file was
  live-appended-to) holds that trailing partial fragment back rather than consuming
  it, so ``next_position`` never lands anywhere but a newline boundary and a
  genuinely truncated final record is simply re-read complete on the next call.

:class:`~blizzard.runner.harness.transcript.TranscriptPosition`'s token is this
module's own JSON: ``{"main": <byte offset>, "sidecars": {<agentId>: <byte offset>}}``
— opaque to every other caller, per the seam's contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from blizzard.runner.harness.internal.claude_code_normalizer import NORMALIZER_VERSION, normalize_lines
from blizzard.runner.harness.transcript import (
    IHarnessTranscriptSource,
    NormalizedTurn,
    SidechainConversation,
    TranscriptBatch,
    TranscriptErrorFactory,
    TranscriptPosition,
    TranscriptReadReason,
)

#: Refuse a pathological file on a cold (``since=None``) read: only the last
#: this-many bytes are read off disk at all, enforced by seeking to the tail before
#: reading — peak memory is bounded by this cap regardless of the file's actual
#: size. Ported verbatim from ``transcripts/internal/jsonl_transcript_repository.py``.
MAX_FILE_BYTES = 64 * 1024 * 1024

#: Bounds one forward (``since`` given) read's total bytes across the main file and
#: every sidecar it reaches — a delta batch is never unbounded just because a fleet
#: worker went quiet for a long stretch. Exhausting it reports ``complete=False``
#: plus a ``next_position`` the caller loops on.
MAX_BATCH_BYTES = 8 * 1024 * 1024


def mangle_cwd(cwd: str) -> str:
    """Claude Code's project-directory name for the absolute spawn cwd ``cwd``.

    Kept only as the multi-match disambiguator, never the primary lookup — see
    ``transcripts/locator.py``'s original docstring for why (unverified/lossy
    third-party mangling); this module's own copy of the same one-line rule.
    """
    return cwd.replace("/", "-")


@dataclass(frozen=True)
class _DecodedPosition:
    main: int
    sidecars: dict[str, int]


def _decode_position(since: TranscriptPosition | None) -> _DecodedPosition:
    """A tolerant decode: a foreign or malformed token degrades to "start over"
    rather than raising — a position is a hint this module minted for itself, and a
    corrupt one is never a caller's fault to crash over."""
    if since is None:
        return _DecodedPosition(main=0, sidecars={})
    try:
        data = json.loads(since.token)
    except (json.JSONDecodeError, TypeError):
        return _DecodedPosition(main=0, sidecars={})
    if not isinstance(data, dict):
        return _DecodedPosition(main=0, sidecars={})
    main = data.get("main")
    sidecars_raw = data.get("sidecars")
    sidecars = (
        {k: v for k, v in sidecars_raw.items() if isinstance(k, str) and isinstance(v, int)}
        if isinstance(sidecars_raw, dict)
        else {}
    )
    return _DecodedPosition(main=main if isinstance(main, int) else 0, sidecars=sidecars)


def _encode_position(main_offset: int, sidecar_offsets: dict[str, int]) -> TranscriptPosition:
    return TranscriptPosition(token=json.dumps({"main": main_offset, "sidecars": sidecar_offsets}, sort_keys=True))


@dataclass(frozen=True)
class _FileRead:
    lines: list[str]
    next_offset: int
    truncated: bool  # the tail cap tripped (`since=None` only)
    hit_budget: bool  # more remained beyond what this call read (forward reads only)


def _read_cold(path: Path) -> _FileRead:
    """``since=None``: tail-seek bound by :data:`MAX_FILE_BYTES`, read to EOF."""
    size = path.stat().st_size
    truncated = size > MAX_FILE_BYTES
    begin = size - MAX_FILE_BYTES if truncated else 0
    with path.open("rb") as f:
        if truncated:
            f.seek(begin)
        raw = f.read()
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if truncated and lines:
        lines = lines[1:]  # a mid-file seek can land mid-line — drop the fragment
    return _FileRead(lines=lines, next_offset=size, truncated=truncated, hit_budget=False)


def _read_forward(path: Path, *, start_offset: int, budget: int) -> _FileRead:
    """A forward read from ``start_offset``, bounded by ``budget`` bytes.

    ``budget <= 0`` makes no progress at all (the shared batch budget was already
    spent by an earlier file this call) — reported via ``hit_budget`` so the caller
    knows this file still has unread content waiting.
    """
    size = path.stat().st_size
    begin = min(start_offset, size)
    if budget <= 0:
        return _FileRead(lines=[], next_offset=begin, truncated=False, hit_budget=begin < size)
    end = min(begin + budget, size)
    with path.open("rb") as f:
        f.seek(begin)
        raw = f.read(end - begin)
    hit_budget = end < size
    last_newline = raw.rfind(b"\n")
    # Consume up to the last complete line; a trailing partial fragment (the file is
    # live-appended-to) is held back so `next_position` never lands mid-line — it is
    # simply re-read complete on a later call. No boundary found at all falls back to
    # consuming the whole chunk (guarantees forward progress over perfect precision
    # for the vanishingly rare single line wider than the batch budget).
    consumed = (last_newline + 1) if last_newline != -1 else len(raw)
    lines = raw[:consumed].decode("utf-8", errors="replace").splitlines()
    return _FileRead(lines=lines, next_offset=begin + consumed, truncated=False, hit_budget=hit_budget)


def _agent_type(turns: list[NormalizedTurn], index: int) -> str | None:
    tool = turns[index].tool
    if tool is None:
        return None
    candidate = tool.input.get("subagent_type")
    return candidate if isinstance(candidate, str) else None


class ClaudeCodeTranscriptSource:
    """Locates and normalizes a session's transcript (plus its sidecars) under
    ``projects_root``.

    ``projects_root`` is a constructor argument (``bzh:dependency-injection``) —
    empty-string defaulting to ``~/.claude/projects`` is resolved once at the
    composition root, never here — so a test injects ``tmp_path`` directly:
    hermetic by construction, no ``HOME`` monkey-patching.
    """

    def __init__(self, projects_root: str, error_factory: TranscriptErrorFactory) -> None:
        self._projects_root = Path(projects_root)
        self._errors = error_factory

    def turns_since(
        self, session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None
    ) -> TranscriptBatch:
        matches = sorted(self._projects_root.glob(f"*/{session_id}.jsonl"))
        if not matches:
            return _unavailable(session_id, "not_found")

        try:
            path = matches[0] if len(matches) == 1 else self._disambiguate(matches, spawn_cwd)
            position = _decode_position(since)
            if since is None:
                main_read = _read_cold(path)
                remaining_budget: int | None = None
            else:
                main_read = _read_forward(path, start_offset=position.main, budget=MAX_BATCH_BYTES)
                remaining_budget = MAX_BATCH_BYTES - (main_read.next_offset - position.main)
        except OSError as exc:
            self._errors.from_io(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return _unavailable(session_id, "unreadable")

        normalized = normalize_lines(main_read.lines)
        hit_budget = main_read.hit_budget
        sidecar_offsets = dict(position.sidecars)

        sidecar_dir = path.parent / session_id / "subagents"
        candidate_agent_ids = sorted(set(normalized.agent_id_by_tool_turn.values()))
        for agent_id in candidate_agent_ids:
            sidecar_path = sidecar_dir / f"agent-{agent_id}.jsonl"
            if not sidecar_path.is_file():
                continue
            if since is not None and (remaining_budget is None or remaining_budget <= 0):
                hit_budget = True
                continue  # unread this round — its offset (if any) carries forward unchanged

            try:
                if since is None:
                    sidecar_read = _read_cold(sidecar_path)
                else:
                    start = sidecar_offsets.get(agent_id, 0)
                    assert remaining_budget is not None
                    sidecar_read = _read_forward(sidecar_path, start_offset=start, budget=remaining_budget)
                    remaining_budget -= sidecar_read.next_offset - start
            except OSError as exc:
                self._errors.from_io(exc, f"sidecar transcript unreadable: {agent_id}", session_id=session_id)
                continue

            hit_budget = hit_budget or sidecar_read.hit_budget
            sidecar_offsets[agent_id] = sidecar_read.next_offset
            sidecar_normalized = normalize_lines(sidecar_read.lines, is_sidechain_file=True)
            for index, aid in normalized.agent_id_by_tool_turn.items():
                if aid != agent_id:
                    continue
                normalized.turns[index] = replace(
                    normalized.turns[index],
                    sidechain=SidechainConversation(
                        agent_id=agent_id,
                        agent_type=_agent_type(normalized.turns, index),
                        link="agent-id",
                        turns=sidecar_normalized.turns,
                    ),
                )

        complete = True if since is None else not hit_budget
        next_position = _encode_position(main_read.next_offset, sidecar_offsets)
        return TranscriptBatch(
            session_id=session_id,
            available=True,
            reason=None,
            turns=normalized.turns,
            unlinked_sidechains=normalized.unlinked_sidechains,
            next_position=next_position,
            complete=complete,
            truncated=main_read.truncated,
            normalizer_version=NORMALIZER_VERSION,
            harness_version=normalized.harness_version,
        )

    def read_raw_lines(self, session_id: str, *, spawn_cwd: str | None) -> list[str]:
        matches = sorted(self._projects_root.glob(f"*/{session_id}.jsonl"))
        if not matches:
            return []
        try:
            path = matches[0] if len(matches) == 1 else self._disambiguate(matches, spawn_cwd)
            return _read_cold(path).lines
        except OSError as exc:
            self._errors.from_io(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return []

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        """``stat().st_size`` on the located transcript, or ``None`` when there is none.

        Deliberately a ``stat``, not a read: the file this measures is the one that
        has grown too large to keep resuming into."""
        matches = sorted(self._projects_root.glob(f"*/{session_id}.jsonl"))
        if not matches:
            return None
        try:
            path = matches[0] if len(matches) == 1 else self._disambiguate(matches, spawn_cwd)
            return path.stat().st_size
        except OSError as exc:
            self._errors.from_io(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return None

    @staticmethod
    def _disambiguate(matches: list[Path], spawn_cwd: str | None) -> Path:
        """Multi-match tie-break: the spawn-cwd hint, else newest by mtime."""
        if spawn_cwd:
            wanted = mangle_cwd(spawn_cwd)
            for match in matches:
                if match.parent.name == wanted:
                    return match
        return max(matches, key=lambda p: p.stat().st_mtime)


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
        normalizer_version=NORMALIZER_VERSION,
        harness_version=None,
    )


# Typecheck-time Protocol/adapter conformance sentinel (the exemplar's shape,
# `../../exemplars/python/repo_pattern.py`). Pyright rejects the return if
# `ClaudeCodeTranscriptSource` drifts from `IHarnessTranscriptSource`.
def _conforms_harness_transcript_source(x: ClaudeCodeTranscriptSource) -> IHarnessTranscriptSource:
    return x
