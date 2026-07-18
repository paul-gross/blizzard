"""The filesystem ``IReadTranscriptRepository`` adapter (issue #29).

The **only** module in ``transcripts/`` that touches ``pathlib``/``glob`` I/O
(``bzh:domain-core``, ``bzh:dependency-inversion``); confined to ``internal/`` —
package-private, not imported outside :mod:`blizzard.runner.transcripts`.

Located by **session-id glob**, not by reconstructing Claude Code's mangled-cwd
directory name: session ids are runner-minted UUID4s, so
``<projects_root>/*/<session_id>.jsonl`` finds the file by name alone — one
``readdir`` over a few hundred directories, immune to the unverified/lossy ``.``/``_``
mangling, and (this is the load-bearing part) the **only** thing that works for
a closed lease — whose binding is always released by the time closure is recorded
(:class:`~blizzard.runner.domain.leases.LeaseActivity` owns that invariant) —
there is no fact left to reconstruct its spawn cwd from. ``spawn_cwd`` is
therefore an optional disambiguation hint, used only to break a multi-match tie
before falling back to newest-by-mtime; it is never the lookup key.

The file bound (:data:`MAX_FILE_BYTES`) is enforced here, not in ``parser.py``: this
is the only module that touches the filesystem, so it is the only place that can
``stat()`` and seek before reading, keeping peak memory bounded by the cap rather
than by the file's actual size — a pathological file is never read in full.
``parser.parse_turns`` stays pure (``bzh:domain-core``): it takes already-read lines,
never a path, and applies only the post-read caps (``MAX_TURNS``/``MAX_BLOCK_CHARS``).
"""

from __future__ import annotations

from pathlib import Path

from blizzard.foundation.logging import get_logger
from blizzard.runner.transcripts.locator import mangle_cwd
from blizzard.runner.transcripts.parser import parse_turns
from blizzard.runner.transcripts.repository import (
    IReadTranscriptRepository,
    Transcript,
    TranscriptErrorFactory,
    TranscriptUnavailable,
)

_log = get_logger("blizzard.runner.transcripts")

#: Refuse a pathological file: only the last this-many bytes are read off disk at
#: all — enforced by seeking to the tail before reading, not by reading the whole
#: file and then discarding the front, so peak memory is bounded by this cap
#: regardless of the file's actual size.
MAX_FILE_BYTES = 32 * 1024 * 1024


class JsonlTranscriptRepository:
    """Locates and parses a session's ``.jsonl`` transcript under ``projects_root``.

    ``projects_root`` is a constructor argument (``bzh:dependency-injection``) —
    empty-string defaulting to ``~/.claude/projects`` is resolved once at the
    composition root (``runner/app.py``), never here — so a test injects ``tmp_path``
    and writes a ``.jsonl`` there directly: hermetic by construction, no ``HOME``
    monkey-patching.
    """

    def __init__(self, projects_root: str, error_factory: TranscriptErrorFactory) -> None:
        self._projects_root = Path(projects_root)
        self._errors = error_factory

    def read_turns(self, session_id: str, *, spawn_cwd: str | None) -> Transcript:
        matches = sorted(self._projects_root.glob(f"*/{session_id}.jsonl"))
        if not matches:
            # Distinguishes "wrong root" from "the agent never wrote one" — the writer's
            # and reader's unset-defaults deliberately differ, so a globbed root that
            # holds nothing is the most likely symptom of the two disagreeing.
            _log.debug("transcript not found", projects_root=str(self._projects_root), session_id=session_id)
            return _unavailable(session_id, "not_found")

        try:
            path = matches[0] if len(matches) == 1 else self._disambiguate(matches, spawn_cwd)
            lines, file_truncated = _read_tail_lines(path)
        except OSError as exc:
            self._errors.from_io(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return _unavailable(session_id, "unreadable")

        parsed = parse_turns(lines)
        return Transcript(
            session_id=session_id,
            available=True,
            reason=None,
            turns=parsed.turns,
            truncated=parsed.truncated or file_truncated,
        )

    def read_raw_lines(self, session_id: str, *, spawn_cwd: str | None) -> list[str]:
        matches = sorted(self._projects_root.glob(f"*/{session_id}.jsonl"))
        if not matches:
            return []
        try:
            path = matches[0] if len(matches) == 1 else self._disambiguate(matches, spawn_cwd)
            lines, _ = _read_tail_lines(path)
        except OSError as exc:
            self._errors.from_io(exc, f"transcript unreadable: {session_id}", session_id=session_id)
            return []
        return lines

    @staticmethod
    def _disambiguate(matches: list[Path], spawn_cwd: str | None) -> Path:
        """Multi-match tie-break: the spawn-cwd hint, else newest by mtime."""
        if spawn_cwd:
            wanted = mangle_cwd(spawn_cwd)
            for match in matches:
                if match.parent.name == wanted:
                    return match
        return max(matches, key=lambda p: p.stat().st_mtime)


def _read_tail_lines(path: Path) -> tuple[list[str], bool]:
    """Read at most the last :data:`MAX_FILE_BYTES` bytes of ``path``, split into lines.

    ``stat()`` first, then seek straight to the tail offset before reading — the file
    is never read in full, so peak memory is bounded by ``MAX_FILE_BYTES``, not by
    file size. A seek into the middle of the file can land mid-line and mid-UTF-8
    codepoint: decoding uses ``errors="replace"`` so a split codepoint at the read
    boundary can never raise, and the first (possibly partial) line after a
    tail-seek is discarded rather than parsed as a real record — this can drop one
    genuine line at the boundary, an acceptable approximation given the size of the
    cap relative to one line.
    """
    size = path.stat().st_size
    truncated = size > MAX_FILE_BYTES
    with path.open("rb") as f:
        if truncated:
            f.seek(size - MAX_FILE_BYTES)
        raw = f.read()
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if truncated and lines:
        lines = lines[1:]
    return lines, truncated


def _unavailable(session_id: str, reason: TranscriptUnavailable) -> Transcript:
    return Transcript(session_id=session_id, available=False, reason=reason, turns=[], truncated=False)


# Typecheck-time Protocol/adapter conformance sentinel (the exemplar's shape,
# `../../exemplars/python/repo_pattern.py`). Pyright rejects the return if
# `JsonlTranscriptRepository` drifts from `IReadTranscriptRepository`.
def _conforms_read_transcript_repository(x: JsonlTranscriptRepository) -> IReadTranscriptRepository:
    return x
