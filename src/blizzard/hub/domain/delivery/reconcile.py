"""The reconcile — a tenant's flow lines converged on where its chunks stand, never on an event a caller sends.

Its first pass submits every chunk ``delivering`` at a hub node naming an action with no open submission for that
visit, oldest entry first, refusing a visit that cannot name its resources and escalating a node naming no action; its
second closes every open submission whose chunk has left its visit, as ``withdrawal_for`` answers. Admission then takes
a turn for every visit next on each flow it holds."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.domain.actions.visit import LandingProject, VisitRefusal, resolve_visit
from blizzard.hub.domain.chunk.flow_writes import FlowFactWritten
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, LandedRepos
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.config.forges import IReadForgeRepository
from blizzard.hub.domain.config.repositories import ConfiguredRepository, IReadRepositoryRecordRepository
from blizzard.hub.domain.delivery.flows import (
    TURN_INTERRUPTED,
    TURN_SUPERSEDED,
    AdmittedTurn,
    LandingTerms,
    Submission,
    Turn,
    TurnEvent,
    Withdrawal,
    held_resources,
    withdrawal_for,
    written_on,
)
from blizzard.hub.domain.delivery.lines import FlowLineFactory
from blizzard.hub.domain.delivery.ports.flows import IHubStepRefusals, IWriteFlows
from blizzard.hub.domain.graph.actions import HubActionCatalogue
from blizzard.hub.domain.graph.model import Graph, IReadManyGraphs, Node
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.domain.projects.model import IReadProjectRepository
from blizzard.hub.tenancy import TenantClosed

_EVENT_TURN_INTERRUPTED: EventLogKind = "turn-interrupted"

_log = get_logger("blizzard.hub.flow_reconcile")


@domain_model
@dataclass(frozen=True)
class LineReconciliation:
    """What one full reconcile of a tenant did: how many visits it submitted and submissions it closed, and the
    arrival floor its next one reads from — the lowest arrival still open, every open submission sitting at or above
    it."""

    submitted: int
    closed: int
    floor: int
    #: Every flow fact the reconcile wrote, in write order, each in its own committed transaction.
    written: tuple[FlowFactWritten, ...] = ()


@domain_model
@dataclass(frozen=True)
class InterruptedTurns:
    """What ending unanswered turns did: how many it ended, and every flow fact those ends wrote, in write order."""

    ended: int = 0
    written: tuple[FlowFactWritten, ...] = ()


@domain_model
@dataclass(frozen=True)
class _Standing:
    """A chunk standing at a hub node that names an action, at the visit's epoch, since ``entered_at``."""

    chunk: Chunk
    graph: Graph
    node: Node
    epoch: int
    entered_at: datetime


class FlowLineReconciler:
    """One tenant's reconcile, admission, and the turns no task answers for. Every flow write goes through
    :class:`IWriteFlows`, and every chunk write through :class:`IHubStepRefusals`."""

    def __init__(
        self,
        *,
        flows: IWriteFlows,
        facts: IReadChunkFactsRepository,
        record: IReadChunkRecordRepository,
        graphs: IReadManyGraphs,
        artifacts: IReadChunkArtifactsRepository,
        repositories: IReadRepositoryRecordRepository,
        forge_records: IReadForgeRepository,
        projects: IReadProjectRepository,
        links: IReadProjectLinks,
        catalogue: HubActionCatalogue,
        refusals: IHubStepRefusals,
        exclusive: IChunkExclusiveWrites,
        lines: FlowLineFactory,
        clock: IClock,
    ) -> None:
        self._flows = flows
        self._exclusive = exclusive
        self._refusals = refusals
        self._facts = facts
        self._record = record
        self._graphs = graphs
        self._artifacts = artifacts
        self._repositories = repositories
        self._forge_records = forge_records
        self._projects = projects
        self._links = links
        self._catalogue = catalogue
        self._clock = clock
        self._lines = lines

    def reconcile(self, floor: int) -> LineReconciliation:
        """Submit every chunk standing at a hub step with no open submission for its visit, then close every open
        submission at or above ``floor`` whose chunk has left its visit. A submission with an open turn is left to
        that turn's end."""
        open_subs = self._flows.open_submissions_from(floor)
        live = [
            chunk_id
            for chunk_id, status in self._facts.load_live_statuses().items()
            if status is ChunkStatus.DELIVERING
        ]
        facts_by_id = self._facts.load_facts_for(live)
        in_line = {(sub.chunk_id, sub.node_id, sub.epoch) for sub in open_subs}
        submitted = [
            sub
            for standing in self._standing(facts_by_id)
            if (standing.chunk.chunk_id, standing.node.node_id, standing.epoch) not in in_line
            and (sub := self._submit_isolated(standing, facts_by_id[standing.chunk.chunk_id])) is not None
        ]
        withdrawn, still_open = self._close_departed(open_subs, facts_by_id)
        arrivals = [sub.arrival for sub in (*still_open, *submitted)]
        return LineReconciliation(
            submitted=len(submitted),
            closed=len(withdrawn),
            floor=min(arrivals) if arrivals else max((sub.arrival + 1 for sub in open_subs), default=floor),
            written=(
                *(fact for sub in submitted for fact in written_on(sub, kind="submitted")),
                *(fact for closure in withdrawn for fact in closure),
            ),
        )

    def recover_turns(self) -> int:
        """End every open turn ``interrupted`` — it ended with the process that ran it, crashed or shut down. Answers
        how many it ended; a turn already ended writes nothing."""
        return self.end_unanswered(started_before=None, running=frozenset()).ended

    def end_unanswered(self, *, started_before: datetime | None, running: frozenset[str]) -> InterruptedTurns:
        """End ``interrupted`` every open turn not in ``running`` that started before ``started_before`` (every one,
        with ``None``) — each in one transaction with its ``turn-ended`` facts, its ``turn-interrupted`` event, and
        the closure ``withdrawal_for`` answers under its chunk's lock. Answers how many it ended and the flow facts it
        wrote, for the caller to announce: each end commits as its lock is let go."""
        turns = [
            turn
            for turn in self._flows.open_turns()
            if turn.turn_id not in running and (started_before is None or turn.started_at < started_before)
        ]
        if not turns:
            return InterruptedTurns()
        subs = {sub.submission_id: sub for sub in self._flows.open_submissions_from(0)}
        node_names = self._node_names(sorted({sub.chunk_id for sub in subs.values()}))
        now = self._clock.now()
        ends: list[tuple[FlowFactWritten, ...]] = []
        for turn in turns:
            sub = subs.get(turn.submission_id)
            if sub is None:
                event = self._interrupted(turn, None, node_name=None, withdrawal=None)
                written = self._flows.end_turn_interrupted(turn.turn_id, withdrawal=None, event=event, at=now)
            else:
                with self._exclusive.locked([sub.chunk_id]) as handle:
                    withdrawal = withdrawal_for(ChunkFacts.or_default(handle.facts(sub.chunk_id)), sub)
                    event = self._interrupted(
                        turn, sub, node_name=node_names.get((sub.chunk_id, sub.node_id)), withdrawal=withdrawal
                    )
                    written = self._flows.end_turn_locked(
                        handle, turn.turn_id, outcome=TURN_INTERRUPTED, closure=withdrawal, event=event, at=now
                    )
            if written is not None:
                ends.append(written)
        return InterruptedTurns(ended=len(ends), written=tuple(fact for end in ends for fact in end))

    @staticmethod
    def _interrupted(
        turn: Turn, sub: Submission | None, *, node_name: str | None, withdrawal: Withdrawal | None
    ) -> TurnEvent:
        return TurnEvent(
            kind=_EVENT_TURN_INTERRUPTED,
            chunk_id=sub.chunk_id if sub else None,
            node_name=node_name,
            message=f"turn {turn.turn_id} ended interrupted: no running task answers for it",
            detail={
                "turn_id": turn.turn_id,
                "submission_id": turn.submission_id,
                "started_at": iso_utc(turn.started_at),
                "withdrawal": withdrawal,
            },
        )

    def admit(self, floor: int, now: datetime) -> list[AdmittedTurn]:
        """Take a turn, oldest arrival first, for every open submission at or above ``floor`` that is next on every
        flow it holds, judged on one bulk read; the store re-checks each under the flows' locks, handed the
        submissions judged ineligible."""
        subs = self._flows.open_submissions_from(floor)
        if not subs:
            return []
        line = self._lines.line(subs, turning=frozenset(turn.submission_id for turn in self._flows.open_turns()))
        passed_over = frozenset(sub.submission_id for sub in subs if not line.eligible(sub, now=now))
        nexts = [sub for sub in subs if line.is_next(sub, now=now)]
        terms = self._candidate_terms(nexts)
        admitted: list[AdmittedTurn] = []
        for sub in nexts:
            turn: Turn | None = self._flows.take_turn(
                sub.submission_id, passed_over=passed_over, terms=terms[sub.submission_id], at=self._clock.now()
            )
            if turn is not None:
                admitted.append(AdmittedTurn(turn, sub))
        return admitted

    def _candidate_terms(self, subs: Sequence[Submission]) -> dict[str, tuple[LandingTerms, ...]]:
        """The terms each submission's repositories would take now, by submission: each held repository's record
        and its position in the chunk's project's order, read fresh. The store writes one only where the visit has
        taken none."""
        if not subs:
            return {}
        records: dict[str, ConfiguredRepository] = {
            record.repository_id: record for record in self._repositories.list_all(include_retired=True)
        }
        chunks = self._record.get_many(sorted({sub.chunk_id for sub in subs}))
        projects: dict[str | None, LandingProject] = {}
        candidates: dict[str, tuple[LandingTerms, ...]] = {}
        for sub in subs:
            chunk = chunks.get(sub.chunk_id)
            project_id = chunk.project_id if chunk is not None else None
            if project_id not in projects:
                projects[project_id] = LandingProject.read(project_id, projects=self._projects, links=self._links)
            positions = projects[project_id].positions
            candidates[sub.submission_id] = tuple(
                LandingTerms.candidate(repository_id, records.get(repository_id), position=positions.get(repository_id))
                for repository_id in sorted({flow.repository_id for flow in sub.flows})
            )
        return candidates

    def escalate_actionless(self) -> int:
        """Escalate every chunk of this tenant standing at a hub node that names no action — the boot step run
        before :meth:`recover_turns`, so a chunk the carry-over left stranded reaches a person without waiting for a
        tick. Answers how many it escalated."""
        chunks = self._record.get_many(self._record.pinned_to_actionless_hub_nodes())
        facts_by_id = self._facts.load_facts_for(list(chunks))
        graphs = self._graphs.get_many(sorted({chunk.graph_id for chunk in chunks.values()}))
        return sum(
            self._escalate_actionless(chunk, facts, graphs.get(chunk.graph_id))
            for chunk_id, chunk in chunks.items()
            if (facts := facts_by_id.get(chunk_id)) is not None
        )

    def _escalate_actionless(self, chunk: Chunk, facts: ChunkFacts, graph: Graph | None) -> bool:
        node = facts.hub_advance_node(graph) if graph is not None else None
        if node is None or node.action is not None or facts.open_escalation() is not None:
            return False
        epoch = facts.latest_epoch() or 0
        if not facts.awaits_exit_from(node, epoch=epoch):
            return False
        return self._refusals.escalate_action_missing(chunk, node, epoch=epoch)

    def _standing(self, facts_by_id: Mapping[str, ChunkFacts]) -> list[_Standing]:
        """Every chunk in ``facts_by_id`` whose newest movement enters a hub node, oldest entry first, the chunk id
        breaking a tie."""
        chunks = self._record.get_many(list(facts_by_id))
        graphs = self._graphs.get_many(sorted({chunk.graph_id for chunk in chunks.values()}))
        standing: list[_Standing] = []
        for chunk_id, facts in facts_by_id.items():
            chunk = chunks.get(chunk_id)
            graph = graphs.get(chunk.graph_id) if chunk is not None else None
            node = _hub_node_of(facts, graph)
            entered_at = _entered_at(facts)
            if chunk is None or graph is None or node is None or entered_at is None:
                continue
            standing.append(_Standing(chunk, graph, node, facts.latest_epoch() or 0, entered_at))
        return sorted(standing, key=lambda s: (s.entered_at, s.chunk.chunk_id))

    def _submit_isolated(self, standing: _Standing, facts: ChunkFacts) -> Submission | None:
        """Submit one visit, logging and skipping it when it raises, so one chunk's failure never holds back the
        rest of the tenant's line. A closed tenant still ends the pass."""
        try:
            if standing.node.action is None:
                self._escalate_actionless(standing.chunk, facts, standing.graph)
                return None
            return self._submit(standing)
        except TenantClosed:
            raise
        except Exception:
            _log.exception("flow reconcile skipped a visit", chunk_id=standing.chunk.chunk_id)
            return None

    def _submit(self, standing: _Standing) -> Submission | None:
        """Submit the visit to the flows it holds — at the next generation when its own turn found its resources
        moved — or refuse it, routing its failure choice, when it cannot name them."""
        chunk, graph, node, epoch = standing.chunk, standing.graph, standing.node, standing.epoch
        generation = self._generation(chunk.chunk_id, node.node_id, epoch)
        if generation is None:
            return None
        artifacts = self._artifacts.load_artifacts(chunk.chunk_id)
        resolved = resolve_visit(
            artifacts,
            action=node.action,
            spec=self._catalogue.get(node.action) if node.action is not None else None,
            project=LandingProject.read(chunk.project_id, projects=self._projects, links=self._links),
            minted_at=chunk.minted_at,
            repositories=self._repositories.list_all(include_retired=True),
            forges=self._forge_records.list_all(include_retired=True),
        )
        if isinstance(resolved, VisitRefusal):
            self._refusals.refuse(
                chunk,
                graph,
                node,
                epoch=epoch,
                step=resolved.step,
                detail=resolved.detail,
                resolution=resolved.resolution,
            )
            return None
        return self._flows.submit(
            chunk.chunk_id,
            node_id=node.node_id,
            epoch=epoch,
            generation=generation,
            resources=held_resources(resolved.resolution, LandedRepos.of(artifacts).names, resolved.spec),
            at=self._clock.now(),
        )

    def _generation(self, chunk_id: str, node_id: str, epoch: int) -> int | None:
        """The generation the visit submits at: 0 when never submitted, the next when its own turn closed its newest
        ``superseded`` — its resources moved — and ``None`` when that one is open or closed otherwise."""
        latest = self._flows.submission_for_visit(chunk_id, node_id=node_id, epoch=epoch)
        if latest is None:
            return 0
        return latest.generation + 1 if latest.closed == TURN_SUPERSEDED else None

    def _close_departed(
        self, open_subs: Sequence[Submission], facts_by_id: Mapping[str, ChunkFacts]
    ) -> tuple[list[tuple[FlowFactWritten, ...]], list[Submission]]:
        """Close each open submission with no open turn whose chunk has left its visit; the ``withdrawn`` facts of
        each closure it wrote, and the submissions it left open."""
        turning = {turn.submission_id for turn in self._flows.open_turns()}
        outside = sorted({sub.chunk_id for sub in open_subs} - facts_by_id.keys())
        facts = {**facts_by_id, **(self._facts.load_facts_for(outside) if outside else {})}
        now = self._clock.now()
        closed: list[tuple[FlowFactWritten, ...]] = []
        still_open: list[Submission] = []
        for sub in open_subs:
            withdrawal = withdrawal_for(ChunkFacts.or_default(facts.get(sub.chunk_id)), sub)
            if sub.submission_id in turning or withdrawal is None:
                still_open.append(sub)
            elif (written := self._flows.close(sub.submission_id, outcome=withdrawal, at=now)) is not None:
                closed.append(written)
        return closed, still_open

    def _node_names(self, chunk_ids: Sequence[str]) -> dict[tuple[str, str], str]:
        """Each named chunk's graph's node names, by ``(chunk_id, node_id)``."""
        return {key: node.name for key, node in self._lines.nodes(chunk_ids).items()}


def _hub_node_of(facts: ChunkFacts, graph: Graph | None) -> Node | None:
    """The hub node the chunk's newest movement enters on ``graph``, or ``None`` off one."""
    node_id = facts.current_node_id()
    node = graph.node_by_id(node_id) if graph is not None and node_id is not None else None
    return node if node is not None and node.is_hub_command_node else None


def _entered_at(facts: ChunkFacts) -> datetime | None:
    """When the chunk's newest movement was recorded — the instant it entered the node it stands at."""
    instants = [
        *(t.recorded_at for t in facts.transitions),
        *(m.recorded_at for m in facts.migrations),
        *(r.recorded_at for r in facts.restarts),
    ]
    return max(instants, default=None)
