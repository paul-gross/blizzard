"""The reconcile — a tenant's flow lines converged on where its chunks stand, never on an event a caller sends.

Its first pass submits every chunk ``delivering`` at a hub node naming an action with no open submission for that
visit, oldest entry first; its second closes every open submission whose chunk has left its visit, as ``withdrawal_for``
answers. It writes nothing to a chunk: a visit the slot would refuse, or a node naming no action, is left to the slot
and the boot pass."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.roles import domain_model
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.domain.actions.visit import LandingProject, VisitRefusal, resolve_visit
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, LandedRepos
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.config.forges import IReadForgeRepository
from blizzard.hub.domain.config.repositories import IReadRepositoryRecordRepository
from blizzard.hub.domain.delivery.flows import Submission, TurnEvent, held_resources, withdrawal_for
from blizzard.hub.domain.delivery.ports.flows import IWriteFlows
from blizzard.hub.domain.graph.actions import HubActionCatalogue
from blizzard.hub.domain.graph.model import Graph, IReadManyGraphs, Node
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.domain.projects.model import IReadProjectRepository

_EVENT_TURN_INTERRUPTED: EventLogKind = "turn-interrupted"


@domain_model
@dataclass(frozen=True)
class LineReconciliation:
    """What one full reconcile of a tenant did: how many visits it submitted and submissions it closed, and the
    arrival floor its next one reads from — the lowest arrival still open, every open submission sitting at or above
    it."""

    submitted: int
    closed: int
    floor: int


@domain_model
@dataclass(frozen=True)
class _Standing:
    """A chunk standing at a hub node that names an action, at the visit's epoch, since ``entered_at``."""

    chunk: Chunk
    node: Node
    epoch: int
    entered_at: datetime


class FlowLineReconciler:
    """One tenant's reconcile, and the turns its last process left open. Every flow write goes through
    :class:`IWriteFlows`; nothing here writes to a chunk."""

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
        clock: IClock,
    ) -> None:
        self._flows = flows
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
            and (sub := self._submit(standing)) is not None
        ]
        closed, still_open = self._close_departed(open_subs, facts_by_id)
        arrivals = [sub.arrival for sub in (*still_open, *submitted)]
        return LineReconciliation(
            submitted=len(submitted),
            closed=closed,
            floor=min(arrivals) if arrivals else max((sub.arrival + 1 for sub in open_subs), default=floor),
        )

    def recover_turns(self) -> int:
        """End every open turn ``interrupted`` — it ended with the process that ran it, crashed or shut down — each
        in one transaction with its ``turn-ended`` facts, its ``turn-interrupted`` event, and the closure
        ``withdrawal_for`` answers for a submission whose chunk has left its visit. Answers how many it ended; a
        turn already ended writes nothing."""
        turns = self._flows.open_turns()
        if not turns:
            return 0
        subs = {sub.submission_id: sub for sub in self._flows.open_submissions_from(0)}
        chunk_ids = sorted({subs[turn.submission_id].chunk_id for turn in turns if turn.submission_id in subs})
        facts_by_id = self._facts.load_facts_for(chunk_ids)
        node_names = self._node_names(chunk_ids)
        now = self._clock.now()
        ended = 0
        for turn in turns:
            sub = subs.get(turn.submission_id)
            withdrawal = withdrawal_for(ChunkFacts.or_default(facts_by_id.get(sub.chunk_id)), sub) if sub else None
            event = TurnEvent(
                kind=_EVENT_TURN_INTERRUPTED,
                chunk_id=sub.chunk_id if sub else None,
                node_name=node_names.get((sub.chunk_id, sub.node_id)) if sub else None,
                message=f"turn {turn.turn_id} ended interrupted: the hub process running it stopped",
                detail={
                    "turn_id": turn.turn_id,
                    "submission_id": turn.submission_id,
                    "started_at": iso_utc(turn.started_at),
                    "withdrawal": withdrawal,
                },
            )
            if self._flows.end_turn_interrupted(turn.turn_id, withdrawal=withdrawal, event=event, at=now):
                ended += 1
        return ended

    def _standing(self, facts_by_id: Mapping[str, ChunkFacts]) -> list[_Standing]:
        """Every chunk in ``facts_by_id`` whose newest movement enters a hub node naming an action, oldest entry
        first, the chunk id breaking a tie."""
        chunks = self._record.get_many(list(facts_by_id))
        graphs = self._graphs.get_many(sorted({chunk.graph_id for chunk in chunks.values()}))
        standing: list[_Standing] = []
        for chunk_id, facts in facts_by_id.items():
            chunk = chunks.get(chunk_id)
            graph = graphs.get(chunk.graph_id) if chunk is not None else None
            node = _hub_node_of(facts, graph)
            entered_at = _entered_at(facts)
            if chunk is None or node is None or node.action is None or entered_at is None:
                continue
            standing.append(_Standing(chunk, node, facts.latest_epoch() or 0, entered_at))
        return sorted(standing, key=lambda s: (s.entered_at, s.chunk.chunk_id))

    def _submit(self, standing: _Standing) -> Submission | None:
        """Submit the visit to the flows it holds, or leave it unsubmitted when the slot's run would refuse it."""
        assert standing.node.action is not None
        spec = self._catalogue.get(standing.node.action)
        if spec is None:
            return None
        chunk = standing.chunk
        artifacts = self._artifacts.load_artifacts(chunk.chunk_id)
        resolved = resolve_visit(
            artifacts,
            project=LandingProject.read(chunk.project_id, projects=self._projects, links=self._links),
            minted_at=chunk.minted_at,
            repositories=self._repositories.list_all(include_retired=True),
            forges=self._forge_records.list_all(include_retired=True),
            lands=spec.lands,
        )
        if isinstance(resolved, VisitRefusal):
            return None
        return self._flows.submit(
            chunk.chunk_id,
            node_id=standing.node.node_id,
            epoch=standing.epoch,
            generation=0,
            resources=held_resources(resolved.resolution, LandedRepos.of(artifacts).names, spec),
            at=self._clock.now(),
        )

    def _close_departed(
        self, open_subs: Sequence[Submission], facts_by_id: Mapping[str, ChunkFacts]
    ) -> tuple[int, list[Submission]]:
        """Close each open submission with no open turn whose chunk has left its visit; how many it closed, and
        the submissions it left open."""
        turning = {turn.submission_id for turn in self._flows.open_turns()}
        outside = sorted({sub.chunk_id for sub in open_subs} - facts_by_id.keys())
        facts = {**facts_by_id, **(self._facts.load_facts_for(outside) if outside else {})}
        now = self._clock.now()
        closed = 0
        still_open: list[Submission] = []
        for sub in open_subs:
            withdrawal = withdrawal_for(ChunkFacts.or_default(facts.get(sub.chunk_id)), sub)
            if sub.submission_id in turning or withdrawal is None:
                still_open.append(sub)
            elif self._flows.close(sub.submission_id, outcome=withdrawal, at=now):
                closed += 1
        return closed, still_open

    def _node_names(self, chunk_ids: Sequence[str]) -> dict[tuple[str, str], str]:
        """Each named chunk's graph's node names, by ``(chunk_id, node_id)``."""
        chunks = self._record.get_many(list(chunk_ids))
        graphs = self._graphs.get_many(sorted({chunk.graph_id for chunk in chunks.values()}))
        return {
            (chunk.chunk_id, node.node_id): node.name
            for chunk in chunks.values()
            if (graph := graphs.get(chunk.graph_id)) is not None
            for node in graph.nodes
        }


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
