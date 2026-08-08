"""The Claude Code ``IHarnessTranscriptSource`` adapter (blizzard#245).

The only module here that touches ``pathlib``/``glob`` I/O (``bzh:dependency-inversion``),
and the one owner of the file-location rules: :meth:`ClaudeCodeTranscriptSource.mangle_cwd`,
the session-id glob, the multi-match tie-break, sidecar discovery, and the tail-seek read.
A :class:`TranscriptPosition` token is this module's own JSON, opaque to every caller."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from blizzard.runner.harness.internal.claude_code_normalizer import NORMALIZER_VERSION, NormalizedFile
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


@dataclass(frozen=True)
class Position:
    """A decoded read cursor: the main file's byte offset, plus one per known sidecar."""

    main: int
    sidecars: dict[str, int]

    @classmethod
    def of(cls, since: TranscriptPosition | None) -> Position:
        """A tolerant decode: a foreign or malformed token degrades to "start over" rather
        than raising — a position is a hint this module minted for itself, and a corrupt one
        is never a caller's fault to crash over."""
        if since is None:
            return cls(main=0, sidecars={})
        try:
            data = json.loads(since.token)
        except (json.JSONDecodeError, TypeError):
            return cls(main=0, sidecars={})
        if not isinstance(data, dict):
            return cls(main=0, sidecars={})
        main = data.get("main")
        sidecars_raw = data.get("sidecars")
        # A negative offset is as malformed as a missing one — clamped to 0 on the same
        # tolerant "start over" path, never an `OSError` raised out of `f.seek()`.
        sidecars = (
            {k: max(v, 0) for k, v in sidecars_raw.items() if isinstance(k, str) and isinstance(v, int)}
            if isinstance(sidecars_raw, dict)
            else {}
        )
        return cls(main=max(main, 0) if isinstance(main, int) else 0, sidecars=sidecars)

    @property
    def token(self) -> TranscriptPosition:
        return TranscriptPosition(token=json.dumps({"main": self.main, "sidecars": self.sidecars}, sort_keys=True))


@dataclass(frozen=True)
class FileRead:
    """One file's whole lines, however far this call got, and how it stopped."""

    lines: list[str]
    next_offset: int
    truncated: bool  # the tail cap tripped (`since=None` only)
    hit_budget: bool  # more remained beyond what this call read (forward reads only)

    @classmethod
    def cold(cls, path: Path) -> FileRead:
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
        lines = cls._decode(raw, consumed)
        if truncated and lines:
            lines = lines[1:]  # a mid-file seek can land mid-line — drop the fragment
        return cls(lines=lines, next_offset=begin + consumed, truncated=truncated, hit_budget=False)

    @classmethod
    def forward(cls, path: Path, *, start_offset: int, budget: int) -> FileRead:
        """A forward read from ``start_offset``, bounded by ``budget`` bytes.

        ``budget <= 0`` makes no progress at all, reported via ``hit_budget``. ``budget`` is
        **not** always the per-call ceiling — a sidecar gets whatever share survived the main
        file — which is why the oversize-record escape below is gated on the full width."""
        size = path.stat().st_size
        begin = min(start_offset, size)
        if budget <= 0:
            return cls(lines=[], next_offset=begin, truncated=False, hit_budget=begin < size)
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
        lines = cls._decode(raw, consumed)
        return cls(lines=lines, next_offset=begin + consumed, truncated=False, hit_budget=hit_budget)

    @staticmethod
    def _decode(raw: bytes, consumed: int) -> list[str]:
        return raw[:consumed].decode("utf-8", errors="replace").splitlines()


class _SidecarJoin:
    """One session's subagent sidecar files, read under a shared byte budget and nested onto
    the main file's already-normalized turns. Mutates ``normalized`` in place."""

    def __init__(
        self,
        errors: TranscriptErrorFactory,
        *,
        session_id: str,
        directory: Path,
        normalized: NormalizedFile,
        offsets: dict[str, int],
        budget: int,
        cold: bool,
    ) -> None:
        self._errors = errors
        self._session_id = session_id
        self._directory = directory
        self._normalized = normalized
        self._cold = cold
        self._budget = budget
        self.offsets = dict(offsets)
        self.hit_budget = False
        self.truncated = False
        self.budget_exhausted = False
        self._turns_by_agent_id: dict[str, list[int]] = {}
        for index, agent_id in normalized.agent_id_by_tool_turn.items():
            self._turns_by_agent_id.setdefault(agent_id, []).append(index)

    def join(self) -> None:
        # The union of three sources, so a sidecar never falls out of consideration the
        # moment its main-file line scrolls out of the current read window.
        candidates = sorted(
            set(self._turns_by_agent_id) | set(self._normalized.discovered_agent_ids) | set(self.offsets)
        )
        for agent_id in candidates:
            path = self._directory / f"agent-{agent_id}.jsonl"
            if not path.is_file():
                # Not yet flushed to disk — still recorded as a live candidate, so a
                # later call can find it once the file exists.
                self.offsets.setdefault(agent_id, 0)
                continue
            if self._budget <= 0:
                self._skip(agent_id, path)
                continue
            read = self._read(agent_id, path)
            if read is None:
                continue
            self.hit_budget = self.hit_budget or read.hit_budget
            self.truncated = self.truncated or read.truncated
            self.offsets[agent_id] = read.next_offset
            self._attach(agent_id, NormalizedFile.of_lines(read.lines, is_sidechain_file=True).turns)

    def _skip(self, agent_id: str, path: Path) -> None:
        if self._cold:
            # Flagged via `sidechain_truncated`, never the panel-facing `truncated`,
            # which reports main-file cuts only.
            self.budget_exhausted = True
            self._errors.budget_skipped(
                "sidecar transcript skipped: shared fan-out budget exhausted",
                session_id=self._session_id,
                agent_id=agent_id,
            )
        else:
            # Only a candidate with genuinely unread bytes makes this batch incomplete;
            # flagging one already read costs a no-op round trip.
            try:
                size = path.stat().st_size
                recorded = self.offsets.get(agent_id, 0)
                start = recorded if recorded <= size else 0
                if start < size:
                    self.hit_budget = True
            except OSError:
                pass
        # Recording it here, even at offset 0, is what keeps a budget-skipped sidecar
        # from falling out of `next_position` entirely.
        self.offsets.setdefault(agent_id, 0)

    def _read(self, agent_id: str, path: Path) -> FileRead | None:
        try:
            if self._cold:
                size = path.stat().st_size
                read = FileRead.cold(path)
                self._budget -= min(size, MAX_FILE_BYTES)
            else:
                start = self.offsets.get(agent_id, 0)
                # Same past-EOF clamp as the main file, for the same reason.
                if start > path.stat().st_size:
                    start = 0
                read = FileRead.forward(path, start_offset=start, budget=self._budget)
                self._budget -= read.next_offset - start
        except OSError as exc:
            # Recovered, not aborted: this sidecar is skipped, but the batch still
            # reports `available=True` — WARNING, not a boundary-failure ERROR.
            self._errors.from_io_recovered(
                exc, "sidecar transcript unreadable", session_id=self._session_id, agent_id=agent_id
            )
            return None
        return read

    def _attach(self, agent_id: str, turns: list[NormalizedTurn]) -> None:
        spawning_indices = self._turns_by_agent_id.get(agent_id)
        if not spawning_indices:
            # Nothing to nest under, so the conversation surfaces unlinked rather than
            # being dropped — what `link="unlinked"` means, for every producer.
            if turns:
                self._normalized.unlinked_sidechains.append(
                    SidechainConversation(agent_id=agent_id, agent_type=None, link="unlinked", turns=turns)
                )
            return
        for index in spawning_indices:
            already_attached = self._normalized.turns[index].sidechain
            if already_attached is not None:
                # The sidecar join is preferred, but the displaced inline conversation
                # surfaces unlinked rather than being lost.
                self._normalized.unlinked_sidechains.append(replace(already_attached, link="unlinked"))
            self._normalized.turns[index] = replace(
                self._normalized.turns[index],
                sidechain=SidechainConversation(
                    agent_id=agent_id, agent_type=self._agent_type(index), link="agent-id", turns=turns
                ),
            )

    def _agent_type(self, index: int) -> str | None:
        tool = self._normalized.turns[index].tool
        candidate = tool.input.get("subagent_type") if tool is not None else None
        return candidate if isinstance(candidate, str) else None


class ClaudeCodeTranscriptSource:
    """Locates and normalizes a session's transcript, plus its sidecars.

    ``projects_root`` is a constructor argument (``bzh:dependency-injection``), resolved
    at the composition root, so this class is hermetic by construction."""

    def __init__(self, projects_root: str, error_factory: TranscriptErrorFactory) -> None:
        self._projects_root = Path(projects_root)
        self._errors = error_factory

    @staticmethod
    def mangle_cwd(cwd: str) -> str:
        """Claude Code's project-directory name for the absolute spawn cwd ``cwd``.

        Undocumented third-party behavior, and lossy (``/a/b-c`` and ``/a-b/c`` mangle
        identically), so it is only ever the multi-match disambiguator."""
        return cwd.replace("/", "-")

    def turns_since(
        self, session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None
    ) -> TranscriptBatch:
        matches = self._matches(session_id)
        if not matches:
            self._errors.not_found(session_id=session_id, projects_root=str(self._projects_root))
            return self._unavailable(session_id, "not_found")

        try:
            path = self._locate(matches, spawn_cwd)
            position = Position.of(since)
            # An offset past the current size would make the read's delta negative and
            # inflate the remaining budget; treated as any other corrupt hint.
            if position.main > path.stat().st_size:
                position = replace(position, main=0)
            if since is None:
                main_read = FileRead.cold(path)
                # Spent purely as a gate on sidecar count — see MAX_BATCH_BYTES.
                remaining_budget = MAX_BATCH_BYTES
            else:
                main_read = FileRead.forward(path, start_offset=position.main, budget=MAX_BATCH_BYTES)
                remaining_budget = MAX_BATCH_BYTES - (main_read.next_offset - position.main)
        except OSError as exc:
            self._errors.from_io(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return self._unavailable(session_id, "unreadable")

        normalized = NormalizedFile.of_lines(main_read.lines)
        sidecars = _SidecarJoin(
            self._errors,
            session_id=session_id,
            directory=path.parent / session_id / "subagents",
            normalized=normalized,
            offsets=position.sidecars,
            budget=remaining_budget,
            cold=since is None,
        )
        sidecars.join()

        hit_budget = main_read.hit_budget or sidecars.hit_budget
        return TranscriptBatch(
            session_id=session_id,
            available=True,
            reason=None,
            turns=normalized.turns,
            unlinked_sidechains=normalized.unlinked_sidechains,
            next_position=Position(main=main_read.next_offset, sidecars=sidecars.offsets).token,
            complete=True if since is None else not hit_budget,
            truncated=main_read.truncated,
            sidechain_truncated=sidecars.budget_exhausted or sidecars.truncated,
            normalizer_version=NORMALIZER_VERSION,
            harness_version=normalized.harness_version,
        )

    def read_raw_lines(self, session_id: str, *, spawn_cwd: str | None) -> list[str]:
        matches = self._matches(session_id)
        if not matches:
            return []
        try:
            return FileRead.cold(self._locate(matches, spawn_cwd)).lines
        except OSError as exc:
            # Recovered, not a boundary failure: an empty reply reads as "no signal".
            self._errors.from_io_recovered(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return []

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        """``stat().st_size`` on the located transcript, or ``None`` when there is none.

        Deliberately a ``stat``, not a read: the file this measures is the one that
        has grown too large to keep resuming into."""
        matches = self._matches(session_id)
        if not matches:
            return None
        try:
            return self._locate(matches, spawn_cwd).stat().st_size
        except OSError as exc:
            # Recovered, not a boundary failure — see `read_raw_lines` above.
            self._errors.from_io_recovered(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return None

    def _matches(self, session_id: str) -> list[Path]:
        return sorted(self._projects_root.glob(f"*/{session_id}.jsonl"))

    @classmethod
    def _locate(cls, matches: list[Path], spawn_cwd: str | None) -> Path:
        """The session's file: the sole match, else the spawn-cwd hint, else newest by mtime."""
        if len(matches) == 1:
            return matches[0]
        if spawn_cwd:
            wanted = cls.mangle_cwd(spawn_cwd)
            for match in matches:
                if match.parent.name == wanted:
                    return match
        return max(matches, key=lambda p: p.stat().st_mtime)

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


# Typecheck-time conformance sentinel (`blizzard-context:/exemplars/python/`): the
# return is rejected if this class drifts from the Protocol.
def _conforms_harness_transcript_source(x: ClaudeCodeTranscriptSource) -> IHarnessTranscriptSource:
    return x
