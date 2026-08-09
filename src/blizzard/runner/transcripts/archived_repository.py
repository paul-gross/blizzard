"""The archived-transcript read seam — a closed lease's hub-stored segments (blizzard#249, D4).

A read through this seam never raises for a transport failure: the panel's route is
200-always with an in-band ``reason`` (``runner/api/transcripts.py``), so a hub outage
must reach :class:`~blizzard.runner.transcripts.service.TranscriptService` as a value, not
an exception. :data:`ArchivedTranscriptStatus` carries the four outcomes Decision 1's
resolution table hinges on — ``"found"`` is the only one a caller renders as hub-sourced;
``"empty"`` and ``"refused"`` are definite answers that fall back to local exactly alike;
``"unreachable"`` is the one candidate for the wire's hub-unreachable state, and only when
local cannot answer either. Read-only by design (``bzh:repository-split``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from blizzard.runner.transcripts.repository import Turn

ArchivedTranscriptStatus = Literal["found", "empty", "refused", "unreachable"]


@dataclass(frozen=True)
class ArchivedTranscript:
    """The hub's answer to one ``(chunk_id, node_id, epoch)`` archived-transcript read.

    ``turns``/``truncated``/``dropped`` are only meaningful when ``status == "found"``;
    every other status carries ``turns=[]``, ``truncated=False``, ``dropped=0``. ``dropped``
    counts turns the hub→panel projection (D5) itself dropped — thinking turns and every
    sidechain, folded away wholesale because the panel's turn model has no slot for either."""

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
