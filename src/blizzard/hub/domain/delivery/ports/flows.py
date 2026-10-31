"""The flows repository seam — submissions, their closures, and turns, per tenant."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.flow_writes import FlowFactWritten
from blizzard.hub.domain.chunk.model import Chunk, CloseEnqueueDecision
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.config.repositories import RepositoryResolution
from blizzard.hub.domain.delivery.flows import (
    FlowFact,
    FlowFactKind,
    FlowLanding,
    FlowOverview,
    FlowResource,
    LandingTerms,
    LandingWrite,
    Submission,
    SubmissionRecord,
    Turn,
    TurnEvent,
    VisitStanding,
    Withdrawal,
)
from blizzard.hub.domain.graph.model import Graph, Node
from blizzard.hub.tenancy import TenantId


class IReadFlows(Protocol):
    """Read-only flows access."""

    def open_submissions_from(self, floor: int) -> list[Submission]:
        """Every open submission whose arrival is at or above ``floor``, in arrival order, each with the flows it
        holds."""
        ...

    def open_turns(self) -> list[Turn]:
        """Every turn of this tenant with no ``ended_at``."""
        ...

    def submission_for_visit(self, chunk_id: str, *, node_id: str, epoch: int) -> Submission | None:
        """The visit's newest-generation submission, open or closed, or ``None`` when it was never submitted."""
        ...

    def submissions_of(self, chunk_id: str) -> list[SubmissionRecord]:
        """Every submission of the chunk's visits, open or closed, in arrival order, each with its closure — bounded
        by the chunk's visits."""
        ...

    def facts_of(self, submission_id: str) -> list[FlowFact]:
        """Every fact the submission wrote, on every flow it holds, in write order."""
        ...

    def turn_outcomes(self, chunk_id: str, *, node_id: str, epoch: int) -> list[str]:
        """The outcome of every ended turn the visit took, across its submissions, newest first — what the stuck
        rule counts."""
        ...

    def visit_standing(self, chunk_id: str, *, node_id: str, epoch: int) -> VisitStanding | None:
        """Where the visit's newest submission stands in the flows it holds, or ``None`` when it has no open
        submission."""
        ...

    def terms_of(self, submission_id: str) -> dict[str, LandingTerms]:
        """The landing terms the submission took, by repository id; empty before its first turn."""
        ...

    def first_turn_at(self, chunk_id: str, *, node_id: str, epoch: int) -> datetime | None:
        """When the visit's first turn started — the earliest across every generation of its submissions — or
        ``None`` before any."""
        ...


class IReadFlowViews(Protocol):
    """What the surfaces read of the tenant's flows: each flow and the newest of its record, a flow's record paged,
    and the open submissions a line is judged over."""

    def flow_overviews(self) -> list[FlowOverview]:
        """Every flow of the tenant, in resource order, each with the newest of its record."""
        ...

    def flow_overview(self, flow_id: str) -> FlowOverview | None:
        """One flow with the newest of its record, or ``None`` when the tenant holds no such flow."""
        ...

    def record_page(self, flow_id: str, *, before: int | None, limit: int) -> list[FlowFact]:
        """At most ``limit`` of the flow's facts, newest first, each with an ``id`` below ``before`` when given."""
        ...

    def open_submissions_on(self, flow_ids: Sequence[str]) -> list[Submission]:
        """Every open submission holding any of ``flow_ids``, in arrival order, each with every flow it holds."""
        ...

    def open_submissions_of(self, chunk_ids: Sequence[str]) -> list[Submission]:
        """Every open submission of the named chunks, in arrival order, each with every flow it holds."""
        ...

    def chunk_records(self, chunk_ids: Sequence[str]) -> dict[str, list[FlowFact]]:
        """Every fact each named chunk's submissions wrote, on every flow, in write order, by chunk id; a chunk that
        never submitted has no entry."""
        ...


class IFlowChangePublisher(Protocol):
    """The live-broadcast half of a flow fact: one ``flow-changed`` frame, published once the fact's write has
    committed."""

    def publish_flow_changed(
        self,
        flow_id: str,
        *,
        kind: FlowFactKind,
        submission_id: str,
        chunk_id: str,
        outcome: str | None = None,
    ) -> int: ...


class IWriteFlows(IReadFlows, Protocol):
    """Read-write flows access. Every write runs under the tenant row lock, so arrivals and closures serialize."""

    def submit(
        self,
        chunk_id: str,
        *,
        node_id: str,
        epoch: int,
        generation: int,
        resources: Sequence[FlowResource],
        at: datetime,
    ) -> Submission | None:
        """Submit the visit at ``generation`` to the flows of ``resources``, creating any flow not yet made, at the
        tenant's next arrival, with a ``submitted`` fact per flow — one transaction. Returns ``None``, writing nothing,
        when that generation of the visit is already submitted."""
        ...

    def close(self, submission_id: str, *, outcome: Withdrawal, at: datetime) -> tuple[FlowFactWritten, ...] | None:
        """Close the submission ``outcome``, with a ``withdrawn`` fact per flow it holds, answering those facts.
        Returns ``None``, writing nothing, when a closure already exists or the submission has an open turn, whose end
        writes the closure."""
        ...

    def take_turn(
        self,
        submission_id: str,
        *,
        passed_over: frozenset[str],
        terms: Sequence[LandingTerms],
        at: datetime,
    ) -> Turn | None:
        """Start a turn of the submission with a ``turn-started`` fact per flow it holds, in one transaction under the
        tenant row lock and each held resource's lock. Returns ``None``, writing nothing, unless the submission is open
        with no open turn, no flow it holds has an open turn, and every open submission ahead of it on those flows is
        in ``passed_over``, the ones the caller judged ineligible — so its flows are taken together or not at all.

        The same transaction gives the submission terms for each repository it holds and has none for: those an
        earlier generation of the visit took, else the candidate in ``terms`` — so a retried turn keeps what its first
        took, and a resubmission keeps what the visit took."""
        ...

    def end_turn_locked(
        self,
        handle: ILockedChunkRead,
        turn_id: str,
        *,
        outcome: str,
        closure: str | None,
        event: TurnEvent | None,
        at: datetime,
    ) -> tuple[FlowFactWritten, ...] | None:
        """End the open turn ``outcome`` on ``handle``'s locked connection (``bzh:store-exclusive-write``), with a
        ``turn-ended`` fact per held flow, ``event`` in the event log, and the submission closed ``closure`` — a
        verdict, or what ``withdrawal_for`` answers under the same lock — unless already closed. The writes routing
        the answer take this handle, so all commit together or not at all. Answers every flow fact it wrote, for the
        caller to announce once the handle's transaction commits — ``None``, writing nothing, if it has ended."""
        ...

    def record_landing(
        self,
        landing: FlowLanding,
        *,
        node_id: str,
        node_name: str,
        epoch: int,
        admission: EpochAdmission,
        repo: str,
        revision: str,
        at: datetime,
        close_decision: CloseEnqueueDecision,
    ) -> LandingWrite:
        """A turn's merge in one transaction admitted past the fence: the ``merged/<repo>`` marker, its
        ``delivery_repo_landed`` row, the close intents, the ``landed`` fact on the landing's flow and its
        ``flow-landed`` event row. A replay that finds the marker writes nothing. Answers whether the fence would have
        refused, and the event row it wrote."""
        ...

    def end_turn_interrupted(
        self, turn_id: str, *, withdrawal: Withdrawal | None, event: TurnEvent, at: datetime
    ) -> tuple[FlowFactWritten, ...] | None:
        """End the open turn ``interrupted``, with a ``turn-ended`` fact per flow its submission holds, ``event`` in
        the tenant's event log, and the submission closed ``withdrawal`` when one is given and no closure exists — one
        transaction. Answers every flow fact it wrote; ``None``, writing nothing, when the turn has already ended."""
        ...


@domain_model
@dataclass(frozen=True)
class LineSignature:
    """A cheap aggregate fingerprint of what a tenant's lines converge on — where its chunks stand: per table, its
    row count and, where it has an integer ``id``, its highest. Every table is append-only, so two equal signatures
    mean no chunk moved, ended, escalated, requeued, paused, or resumed. No per-row content is read."""

    marks: tuple[tuple[str, int, int | None], ...]


class IReadFlowLinesCorpus(Protocol):
    """The hub-wide change probe the flow driver gates each tenant's reconcile on."""

    def flow_lines_signature(self) -> Mapping[TenantId, LineSignature]:
        """A one-statement, hub-wide read of each tenant's :class:`LineSignature`, closed tenants included. A tenant
        holding none of those rows has no entry."""
        ...


class IHubStepRefusals(Protocol):
    """What the reconcile writes to a chunk it cannot submit: a visit refused before it names its resources routes its
    failure choice, and a hub node naming no action escalates its chunk at the visit's epoch plus one."""

    def refuse(
        self,
        chunk: Chunk,
        graph: Graph,
        node: Node,
        *,
        epoch: int,
        step: str,
        detail: str,
        resolution: RepositoryResolution | None = None,
    ) -> object:
        """Record why the visit cannot run as its ``hub-log.<step>`` artifact and route its failure choice."""
        ...

    def escalate_action_missing(self, chunk: Chunk, node: Node, *, epoch: int) -> bool:
        """Escalate a chunk standing at ``node``, which names no action; whether this call recorded the escalation."""
        ...
