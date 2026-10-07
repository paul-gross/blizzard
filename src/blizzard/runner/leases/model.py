"""The lease models: a lease at mint, joined with its node context, and joined with its closure fact.

Plain data every lease seam module depends on (``bzh:domain-core``)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.identity import SessionReference


@domain_model
@dataclass(frozen=True)
class WorkRefStamp:
    """One work ref as the mint's envelope delivered it; ``label`` is the hub-rendered source-native
    token, ``None`` when no configured source rendered one."""

    source: str
    ref: str
    label: str | None = None


@domain_model
@dataclass(frozen=True)
class NewLease:
    """A node-step lease at mint — before the worker exists."""

    lease_id: str
    chunk_id: str
    graph_id: str
    node_id: str
    node_name: str
    epoch: int
    retries_max: int
    created_at: datetime
    # Session and configuration stamps, from the mint's `lease_context`. `None` means *unknown*.
    session_name: str | None = None
    resolved_model: str | None = None
    resolved_effort: str | None = None
    resolved_compaction_window: str | None = None
    # What the envelope named at mint. `None` means *unknown*, never a value.
    graph_name: str | None = None
    work_refs: tuple[WorkRefStamp, ...] | None = None


@domain_model
@dataclass(frozen=True)
class PoolHead:
    """A named session pool's current head. ``resolved_model``/
    ``resolved_effort`` are the head's own **stamps**, not a fresh resolution; ``None``
    on either means *unknown*, never a value."""

    session_id: str
    lease_id: str
    resolved_model: str | None
    resolved_effort: str | None
    harness_id: str

    @property
    def session(self) -> SessionReference:
        return SessionReference(harness_id=self.harness_id, session_id=self.session_id)


@domain_model
@dataclass(frozen=True)
class Lease:
    """A lease joined with its node context — the loop's per-attempt fact.

    ``pid`` / ``process_start_time`` / ``session_id`` are ``None`` until spawn-return."""

    lease_id: str
    chunk_id: str
    graph_id: str
    node_id: str
    node_name: str
    epoch: int
    retries_max: int
    created_at: datetime
    # Session stamps, read back. `None` means *unknown*, never a value.
    session_name: str | None = None
    resolved_model: str | None = None
    resolved_effort: str | None = None
    resolved_compaction_window: str | None = None
    graph_name: str | None = None
    work_refs: tuple[WorkRefStamp, ...] | None = None
    pid: int | None = None
    process_start_time: str | None = None
    session_id: str | None = None
    harness_id: str | None = None
    # The owned process group, recorded alongside `pid`, never inferred from it.
    pgid: int | None = None

    @property
    def session(self) -> SessionReference | None:  # ast-grep-ignore: bzh:property-delegates
        """The typed concrete-session identity, absent until spawn-return."""
        if self.session_id is None:
            return None
        if self.harness_id is None:
            raise ValueError(f"lease {self.lease_id} has session_id {self.session_id!r} but no recorded harness_id")
        return SessionReference(harness_id=self.harness_id, session_id=self.session_id)


@domain_model
@dataclass(frozen=True)
class ClosedLease:
    """A lease joined with its closure fact — the panel's recent-history read.
    ``reason`` is the closure vocabulary: ``transitioned`` | ``reaped`` | ``failed`` |
    ``escalated`` | ``parked`` | ``released`` | ``owner-unresolvable-mint`` | ``no-acceptable-harness-mint``
    (both zero-budget, minted only to escalate a resume owner or mint selection that failed)."""

    lease: Lease
    reason: str
    closed_at: datetime
