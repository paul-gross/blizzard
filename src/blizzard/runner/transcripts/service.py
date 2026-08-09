"""The transcript route's domain read model (issue #29) — resolves a lease's transcript to
a home (blizzard#249, D1).

Holds only read-only store/repository/archived seams (``bzh:repository-split``), so a
controller may hold this service directly (``bzh:controller-read-only``). ``store.lease(lease_id)``
**spans closure** — unlike ``active_lease``, which filters to unclosed leases — because a
transcript outlives its lease and a closed lease's transcript must stay readable.

Home selection keys on the hub's segment index, not on runner-local ack state: the hub
holding a segment *is* the ack (``bzh:facts-not-status``). An **open** lease
(``store.active_lease`` is not ``None``) is never asked of the hub at all — local, live,
unchanged. A **closed** lease asks the hub first; its segments, when found, win. Every
other hub answer — holds nothing, refuses, or is unreachable while local still answers —
falls back to local, and the wire's ``hub_unreachable`` flag is set only in the one
remaining case: the hub is unreachable *and* local cannot answer either."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.store.repository import IReadRunnerStore, LeaseRecord
from blizzard.runner.transcripts.archived_repository import IReadArchivedTranscriptRepository
from blizzard.runner.transcripts.repository import IReadTranscriptRepository, Transcript

#: Which side answered a resolved transcript (D1) — the wire's ``provenance`` field.
TranscriptProvenance = Literal["local", "archived"]


@dataclass(frozen=True)
class ResolvedTranscript:
    """A lease's transcript, resolved to a home per Decision 1. ``hub_unreachable`` is
    ``True`` only when a closed lease's hub could not be asked *and* local cannot answer
    either; ``dropped`` counts turns the hub→panel projection (D5) dropped, and is zero on
    every local read — the local path's own pre-existing narrowing predates this change and
    stays unreported."""

    transcript: Transcript
    provenance: TranscriptProvenance
    hub_unreachable: bool
    dropped: int


class TranscriptService:
    """Resolves a lease id to its transcript, per Decision 1's home-selection table —
    ``None`` iff no such lease ever existed."""

    def __init__(
        self,
        store: IReadRunnerStore,
        transcripts: IReadTranscriptRepository,
        archived: IReadArchivedTranscriptRepository,
        workspace_root: str,
    ) -> None:
        self._store = store
        self._transcripts = transcripts
        self._archived = archived
        self._workspace_root = workspace_root

    def for_lease(self, lease_id: str) -> ResolvedTranscript | None:
        """The lease's resolved transcript, or ``None`` when no lease with this id ever
        existed — never for a lease that exists but has no session yet or no transcript
        anywhere, which are ``ResolvedTranscript(transcript=Transcript(available=False, …))``."""
        lease = self._store.lease(lease_id)
        if lease is None:
            return None
        if lease.session_id is None:
            # Minted at FILL, spawn-return not yet recorded — the agent has not started a
            # session yet, on either side. Ordinary, not an error.
            transcript = Transcript(session_id=None, available=False, reason="spawning", turns=[], truncated=False)
            return ResolvedTranscript(transcript=transcript, provenance="local", hub_unreachable=False, dropped=0)

        if self._store.active_lease(lease_id) is not None:
            # Open: local, live — never asked of the hub (D1).
            local = self._read_local(lease)
            return ResolvedTranscript(transcript=local, provenance="local", hub_unreachable=False, dropped=0)

        # Closed: the hub's segment index is the ack, so ask it first (D1). A refusal or
        # an empty index is a definite answer, resolved to local exactly like "holds
        # nothing" — never a transport failure.
        archived = self._archived.read_turns(chunk_id=lease.chunk_id, node_id=lease.node_id, epoch=lease.epoch)
        if archived.status == "found":
            transcript = Transcript(
                session_id=lease.session_id,
                available=True,
                reason=None,
                turns=archived.turns,
                truncated=archived.truncated,
            )
            return ResolvedTranscript(
                transcript=transcript, provenance="archived", hub_unreachable=False, dropped=archived.dropped
            )

        local = self._read_local(lease)
        hub_unreachable = archived.status == "unreachable" and not local.available
        return ResolvedTranscript(transcript=local, provenance="local", hub_unreachable=hub_unreachable, dropped=0)

    def _read_local(self, lease: LeaseRecord) -> Transcript:
        assert lease.session_id is not None
        bindings = self._store.bindings_for_chunk(lease.chunk_id)
        # A closed lease's bindings are already released, so `bindings_for_chunk` returns `[]` and the
        # hint is legitimately `None`; the primary by-session-id lookup does not need it.
        fallback_workdir = bindings[0].workdir if bindings else None
        spawn_cwd = SpawnCwd(self._workspace_root, fallback_workdir).path
        return self._transcripts.read_turns(lease.session_id, spawn_cwd=spawn_cwd)
