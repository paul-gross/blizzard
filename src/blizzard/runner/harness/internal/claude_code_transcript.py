"""The Claude Code ``IHarnessTranscriptSource`` adapter (blizzard#245).

The only module here that touches ``pathlib``/``glob`` I/O (``bzh:dependency-inversion``),
and the one owner of the file-location rules: ``mangle_cwd``, the session-id glob, the
multi-match tie-break, sidecar discovery, and the tail-seek read. A
:class:`TranscriptPosition` token is this module's own JSON, opaque to every caller."""

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

#: The tail cap on any ONE file a cold read touches. **Not** a bound on peak in-memory
#: footprint: normalization multiplies raw bytes read by a measured 4-5x.
MAX_FILE_BYTES = 64 * 1024 * 1024

#: One forward read's total bytes, also spent as a cold read's sidecar fan-out gate —
#: checked before admitting a sidecar, not debited as it reads.
MAX_BATCH_BYTES = 8 * 1024 * 1024


def mangle_cwd(cwd: str) -> str:
    """Claude Code's project-directory name for the absolute spawn cwd ``cwd``.

    Undocumented third-party behavior, and lossy (``/a/b-c`` and ``/a-b/c`` mangle
    identically), so it is only ever the multi-match disambiguator."""
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
    # A negative offset is as malformed as a missing one — clamped to 0 on the same
    # tolerant "start over" path, never an `OSError` raised out of `f.seek()`.
    sidecars = (
        {k: max(v, 0) for k, v in sidecars_raw.items() if isinstance(k, str) and isinstance(v, int)}
        if isinstance(sidecars_raw, dict)
        else {}
    )
    return _DecodedPosition(main=max(main, 0) if isinstance(main, int) else 0, sidecars=sidecars)


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
    # A trailing partial line is held back from both the returned lines and the minted
    # offset: an offset landing mid-record would lose that record permanently.
    last_newline = raw.rfind(b"\n")
    consumed = last_newline + 1 if last_newline != -1 else 0
    lines = raw[:consumed].decode("utf-8", errors="replace").splitlines()
    if truncated and lines:
        lines = lines[1:]  # a mid-file seek can land mid-line — drop the fragment
    return _FileRead(lines=lines, next_offset=begin + consumed, truncated=truncated, hit_budget=False)


def _read_forward(path: Path, *, start_offset: int, budget: int) -> _FileRead:
    """A forward read from ``start_offset``, bounded by ``budget`` bytes.

    ``budget <= 0`` makes no progress at all, reported via ``hit_budget``. ``budget`` is
    **not** always the per-call ceiling — a sidecar gets whatever share survived the main
    file — which is why the oversize-record escape below is gated on the full width."""
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
    # A FULL-width window with no newline proves the record is at least that wide, so it
    # is force-consumed to guarantee forward progress; a narrower budget proves nothing.
    if last_newline != -1:
        consumed = last_newline + 1
    elif hit_budget and budget >= MAX_BATCH_BYTES:
        consumed = len(raw)
    else:
        consumed = 0
    lines = raw[:consumed].decode("utf-8", errors="replace").splitlines()
    return _FileRead(lines=lines, next_offset=begin + consumed, truncated=False, hit_budget=hit_budget)


def _agent_type(turns: list[NormalizedTurn], index: int) -> str | None:
    tool = turns[index].tool
    if tool is None:
        return None
    candidate = tool.input.get("subagent_type")
    return candidate if isinstance(candidate, str) else None


class ClaudeCodeTranscriptSource:
    """Locates and normalizes a session's transcript, plus its sidecars.

    ``projects_root`` is a constructor argument (``bzh:dependency-injection``), resolved
    at the composition root, so this class is hermetic by construction."""

    def __init__(self, projects_root: str, error_factory: TranscriptErrorFactory) -> None:
        self._projects_root = Path(projects_root)
        self._errors = error_factory

    def turns_since(
        self, session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None
    ) -> TranscriptBatch:
        matches = sorted(self._projects_root.glob(f"*/{session_id}.jsonl"))
        if not matches:
            self._errors.not_found(session_id=session_id, projects_root=str(self._projects_root))
            return _unavailable(session_id, "not_found")

        try:
            path = matches[0] if len(matches) == 1 else self._disambiguate(matches, spawn_cwd)
            position = _decode_position(since)
            # An offset past the current size would make the read's delta negative and
            # inflate `remaining_budget`; treated as any other corrupt hint.
            if position.main > path.stat().st_size:
                position = replace(position, main=0)
            if since is None:
                main_read = _read_cold(path)
                # Spent purely as a gate on sidecar count — see MAX_BATCH_BYTES.
                remaining_budget = MAX_BATCH_BYTES
            else:
                main_read = _read_forward(path, start_offset=position.main, budget=MAX_BATCH_BYTES)
                remaining_budget = MAX_BATCH_BYTES - (main_read.next_offset - position.main)
        except OSError as exc:
            self._errors.from_io(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return _unavailable(session_id, "unreadable")

        normalized = normalize_lines(main_read.lines)
        hit_budget = main_read.hit_budget
        sidecar_budget_exhausted = False
        sidecar_truncated = False
        sidecar_offsets = dict(position.sidecars)

        sidecar_dir = path.parent / session_id / "subagents"
        # The union of three sources, so a sidecar never falls out of consideration the
        # moment its main-file line scrolls out of the current read window.
        index_by_agent_id: dict[str, list[int]] = {}
        for index, aid in normalized.agent_id_by_tool_turn.items():
            index_by_agent_id.setdefault(aid, []).append(index)
        candidate_agent_ids = sorted(
            set(index_by_agent_id) | set(normalized.discovered_agent_ids) | set(position.sidecars)
        )
        for agent_id in candidate_agent_ids:
            sidecar_path = sidecar_dir / f"agent-{agent_id}.jsonl"
            if not sidecar_path.is_file():
                # Not yet flushed to disk — still recorded as a live candidate, so a
                # later call can find it once the file exists.
                sidecar_offsets.setdefault(agent_id, 0)
                continue
            if remaining_budget <= 0:
                if since is not None:
                    # Only a candidate with genuinely unread bytes makes this batch
                    # incomplete; flagging one already read costs a no-op round trip.
                    try:
                        sidecar_size = sidecar_path.stat().st_size
                        recorded = sidecar_offsets.get(agent_id, 0)
                        start = recorded if recorded <= sidecar_size else 0
                        if start < sidecar_size:
                            hit_budget = True
                    except OSError:
                        pass
                else:
                    # Flagged via `sidechain_truncated`, never the panel-facing
                    # `truncated`, which reports main-file cuts only.
                    sidecar_budget_exhausted = True
                    self._errors.budget_skipped(
                        "sidecar transcript skipped: shared fan-out budget exhausted",
                        session_id=session_id,
                        agent_id=agent_id,
                    )
                # Recording it here, even at offset 0, is what keeps a budget-skipped
                # sidecar from falling out of `next_position` entirely.
                sidecar_offsets.setdefault(agent_id, 0)
                continue

            try:
                if since is None:
                    sidecar_size = sidecar_path.stat().st_size
                    sidecar_read = _read_cold(sidecar_path)
                    remaining_budget -= min(sidecar_size, MAX_FILE_BYTES)
                else:
                    start = sidecar_offsets.get(agent_id, 0)
                    # Same past-EOF clamp as the main file above, for the same reason.
                    if start > sidecar_path.stat().st_size:
                        start = 0
                    sidecar_read = _read_forward(sidecar_path, start_offset=start, budget=remaining_budget)
                    remaining_budget -= sidecar_read.next_offset - start
            except OSError as exc:
                # Recovered, not aborted: this sidecar is skipped, but the batch still
                # reports `available=True` — WARNING, not a boundary-failure ERROR.
                self._errors.from_io_recovered(
                    exc, "sidecar transcript unreadable", session_id=session_id, agent_id=agent_id
                )
                continue

            hit_budget = hit_budget or sidecar_read.hit_budget
            sidecar_truncated = sidecar_truncated or sidecar_read.truncated
            sidecar_offsets[agent_id] = sidecar_read.next_offset
            sidecar_normalized = normalize_lines(sidecar_read.lines, is_sidechain_file=True)
            spawning_indices = index_by_agent_id.get(agent_id)
            if not spawning_indices:
                # Nothing to nest under, so the conversation surfaces unlinked rather
                # than being dropped — what `link="unlinked"` means, for every producer.
                if sidecar_normalized.turns:
                    normalized.unlinked_sidechains.append(
                        SidechainConversation(
                            agent_id=agent_id, agent_type=None, link="unlinked", turns=sidecar_normalized.turns
                        )
                    )
                # `sidecar_normalized.unlinked_sidechains` is always empty here.
                continue
            for index in spawning_indices:
                already_attached = normalized.turns[index].sidechain
                if already_attached is not None:
                    # The sidecar join is preferred, but the displaced inline
                    # conversation surfaces unlinked rather than being lost.
                    normalized.unlinked_sidechains.append(replace(already_attached, link="unlinked"))
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
            sidechain_truncated=sidecar_budget_exhausted or sidecar_truncated,
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
            # Recovered, not a boundary failure: an empty reply reads as "no signal".
            self._errors.from_io_recovered(exc, f"transcript unreadable: {session_id}", session_id=session_id)
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
            # Recovered, not a boundary failure — see `read_raw_lines` above.
            self._errors.from_io_recovered(exc, f"transcript unreadable: {session_id}", session_id=session_id)
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
        sidechain_truncated=False,
        normalizer_version=NORMALIZER_VERSION,
        harness_version=None,
    )


# Typecheck-time conformance sentinel (`blizzard-context:/exemplars/python/`): the
# return is rejected if this class drifts from the Protocol.
def _conforms_harness_transcript_source(x: ClaudeCodeTranscriptSource) -> IHarnessTranscriptSource:
    return x
