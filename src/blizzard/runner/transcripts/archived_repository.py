"""The archived-transcript read seam — a closed lease's hub-stored segments (blizzard#249, D4).

Never raises on a transport failure — reaches the caller as a value instead (``bzh:repository-split``).
:data:`ArchivedTranscriptStatus`'s four outcomes drive Decision 1: only ``"found"`` is
hub-sourced, ``"empty"``/``"refused"`` fall back to local, and ``"unreachable"`` becomes
the wire's hub-unreachable state only when local can't answer either."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from blizzard.runner.transcripts.repository import Turn

ArchivedTranscriptStatus = Literal["found", "empty", "refused", "unreachable"]


@dataclass(frozen=True)
class ArchivedTranscript:
    """The hub's answer to one ``(chunk_id, node_id, epoch)`` archived-transcript read.
    ``turns``/``truncated``/``dropped`` are meaningful only when ``status == "found"``,
    else empty/``False``/``0``. ``dropped`` counts turns the D5 projection folded away."""

    status: ArchivedTranscriptStatus
    turns: list[Turn]
    truncated: bool
    dropped: int


class IReadArchivedTranscriptRepository(Protocol):
    """The archived-transcript lookup seam. Read-only (``bzh:repository-split``)."""

    def read_turns(self, *, chunk_id: str, node_id: str, epoch: int) -> ArchivedTranscript:
        """The lease's hub-stored transcript, folded across every spawn generation under
        this ``(chunk_id, node_id, epoch)`` (D2) — never raises; a transport failure, a
        malformed body, or an unexpected status all resolve to ``status="unreachable"``."""
        ...
