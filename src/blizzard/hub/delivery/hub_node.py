"""Where a hub step's answer goes — the routing a turn's end writes (``bzh:deterministic-shell``).

:class:`HubNodeRouter` maps an answer to an authored edge, records a wait, bounces an incomplete delivery, and
escalates to a person what no edge routes. Every chunk write it makes for a turn goes in one locked transaction with
the turn's end, which the :class:`ITurnEnd` it is handed writes; with no turn — a refusal at submit, an actionless
node — the same writes commit alone. A refusal, and an answer no edge routes, decide inside that one transaction from
the chunk's facts and the visit's artifacts re-read under its lock, and publish their events once it commits."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.clock import IClock
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.foundation.wait_kinds import WaitKind
from blizzard.hub.domain.actions.declarations import GitCommits
from blizzard.hub.domain.actions.effects import ActionEscalation, ForgeAuthReason, MissingCapability
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.delivery_read import DeliveryRead, DeliverySources
from blizzard.hub.domain.chunk.event_log import EventLogService, LoggedEvent, close_skipped_events
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkFacts,
    CloseEnqueueDecision,
    decide_close_enqueue,
)
from blizzard.hub.domain.chunk.no_code import finished_repos
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.decisions import IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.escalations import BounceEscalationOutcome, IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites, ILockedChunkRead
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.hub_exec import IWriteChunkHubExecRepository
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository, MigrationWrite
from blizzard.hub.domain.chunk.terminal import terminal_under_lock
from blizzard.hub.domain.config.forges import ConfiguredForge, IReadForgeRepository
from blizzard.hub.domain.config.repositories import (
    IReadRepositoryRecordRepository,
    RepositoryOutcome,
    RepositoryResolution,
    ResolvedRepository,
    resolve_chunk_repositories,
)
from blizzard.hub.domain.delivery.flows import TURN_ESCALATED
from blizzard.hub.domain.delivery.landing_projects import read_landing_project
from blizzard.hub.domain.delivery.ports.flows import IHubStepRefusals
from blizzard.hub.domain.execution.completion import Landing, MigrationTargets, UnresolvableTarget
from blizzard.hub.domain.execution.gate_arrival import open_graph_gate_on_arrival
from blizzard.hub.domain.graph.model import (
    DEFAULT_BOUNCE_CAP,
    HUB_DEFAULT_FAILURE_CHOICE,
    RESERVED_TERMINAL,
    FollowLatest,
    Graph,
    IReadGraphRepository,
    Node,
)
from blizzard.hub.domain.kernel.forge_refusals import forge_auth_refused_detail
from blizzard.hub.domain.kernel.transactions import IHubTransactions, LockedTransaction
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.domain.projects.model import IReadProjectRepository
from blizzard.hub.domain.projects.targets import ChunkTargets, TargetChunk, branches
from blizzard.hub.work_sources.source import IWorkSourceRegistry

_HUB_RUNNER_ID = "hub"
# Once-per-(node, epoch) dedupe key for an outcome with no authored edge.
_UNROUTABLE_ARTIFACT_NAME = "hub-unroutable-outcome"
_EVENT_UNROUTABLE_OUTCOME: EventLogKind = "hub-node-unroutable-outcome"
_EVENT_ACTION_MISSING: EventLogKind = "hub-action-missing"
_EVENT_CAPABILITY_MISSING: EventLogKind = "forge-capability-missing"
_CAPABILITY_MISSING_STEP = "forge-capability-missing"
_EVENT_FORGE_AUTH_REFUSED: EventLogKind = "forge-auth-refused"
HUB_LOG_PREFIX = "hub-log."
_PENDING = "pending"
#: The event each resolution outcome records — ``None`` where the resolution stands. Every refused
#: outcome records its ``hub-log.<outcome>`` artifact before any pass is taken.
REFUSAL_EVENT: Mapping[RepositoryOutcome, EventLogKind | None] = {
    RepositoryOutcome.RESOLVED: None,
    RepositoryOutcome.NOTHING_TO_RESOLVE: None,
    RepositoryOutcome.UNRESOLVED: "repository-unresolved",
    RepositoryOutcome.NOT_LINKED: "repository-not-linked",
}


@domain_model
@dataclass(frozen=True)
class BouncedAction:
    """The hub action whose pass answered the choice a delivery bounce routed, and the ``hub-log.<action>`` asset
    holding its log — ``None`` when the pass logged nothing."""

    name: str
    choice: str
    log_artifact: str | None

    def payload(self) -> dict[str, object]:
        return {"name": self.name, "choice": self.choice, "log_artifact": self.log_artifact}


@domain_model
@dataclass(frozen=True)
class BounceEnvelope:
    """A hub node's kick-back envelope: why delivery did not complete."""

    cause: str
    detail: str
    unlanded: list[dict[str, object]]
    action: BouncedAction | None

    @classmethod
    def of(
        cls,
        *,
        cause: str,
        summary: str,
        commits: list[dict[str, str]],
        sources: DeliverySources,
        action: BouncedAction | None,
    ) -> BounceEnvelope:
        landed = finished_repos(sources.markers, sources.markers, commits)
        read = DeliveryRead.of(ChunkFacts(minted=True), sources)
        prs = {p.repo: p for p in read.open_prs}
        unlanded: list[dict[str, object]] = []
        for commit in commits:
            repo = commit["repo"]
            if repo in landed:
                continue
            pr = prs.get(repo)
            unlanded.append(
                {
                    "repo": repo,
                    "branch": commit["branch"],
                    "commit": commit["commit"],
                    "pr": {"number": pr.number, "url": pr.url} if pr else None,
                }
            )
        names = ", ".join(
            f"PR #{pr['number']} in {u['repo']}" if isinstance(pr := u["pr"], dict) else str(u["repo"])
            for u in unlanded
        )
        if action is None:
            detail = f"{summary} (not landed: {names})" if names else summary
        else:
            detail = f"{names or summary} did not land: `{action.name}` answered {action.choice}"
        return cls(cause=cause, detail=detail, unlanded=unlanded, action=action)

    def serialize(self) -> str:
        return json.dumps(
            {
                "cause": self.cause,
                "detail": self.detail,
                "unlanded": self.unlanded,
                "action": self.action.payload() if self.action else None,
            },
            ensure_ascii=False,
        )


@domain_model
@dataclass(frozen=True)
class _StandingVisit:
    """A hub visit its chunk still stands at, as read under the chunk's lock, with the chunk's artifacts read there."""

    node_id: str
    epoch: int
    artifacts: tuple[StoredArtifact, ...]

    def first(self, name: str) -> bool:
        """Whether the visit has recorded no ``name`` artifact yet — so the one written now is announced."""
        return not any(a.node_id == self.node_id and a.epoch == self.epoch and a.name == name for a in self.artifacts)


@domain_model
@dataclass(frozen=True)
class _Migrate:
    """A cross-graph edge onto an enabled graph: the chunk migrates to ``landing``."""

    landing: Landing

    def turn_outcome(self, choice: str) -> str:
        """The outcome a turn routing ``choice`` here ends on."""
        return choice


@domain_model
@dataclass(frozen=True)
class _Escalate:
    """A cross-graph edge naming no enabled graph: the chunk is escalated with ``escalation``, routing nothing."""

    escalation: ActionEscalation

    def turn_outcome(self, choice: str) -> str:
        """The outcome a turn routing ``choice`` here ends on."""
        return TURN_ESCALATED


@domain_model
@dataclass(frozen=True)
class _Unroutable:
    """A choice no edge routes, or — ``to_node`` — one routed to a node the graph lacks: announced once per visit,
    and the chunk escalated."""

    to_node: str | None

    def turn_outcome(self, choice: str) -> str:
        """The outcome a turn routing ``choice`` here ends on."""
        return TURN_ESCALATED


@domain_model
@dataclass(frozen=True)
class _Drift:
    """The follow-latest inputs read before the lock for a pin whose policy is on and whose name has a strictly newer
    enabled mint: the policy, and that mint. :meth:`MigrationTargets.of` decides from them under the lock."""

    policy: FollowLatest
    newest: Graph


@domain_model
@dataclass(frozen=True)
class _Transition:
    """A node edge: the chunk transitions to ``to_node_id``, the id of the node the edge names ``to_node_name`` — or
    the reserved terminal. ``drift`` is set when the pin could follow the latest mint at this transition."""

    to_node_id: str
    to_node_name: str
    drift: _Drift | None = None

    def turn_outcome(self, choice: str) -> str:
        """The outcome a turn routing ``choice`` here ends on."""
        return choice


#: Where a choice routes from its node, resolved before the routing's transaction opens.
type _Destination = _Migrate | _Escalate | _Unroutable | _Transition


class ITurnEnd(Protocol):
    """The end of the turn a routing belongs to, written on the routing's own locked transaction."""

    def end(self, transaction: LockedTransaction, *, outcome: str, closure: str | None) -> None:
        """End the turn ``outcome`` in ``transaction``, closing its submission ``closure`` — a verdict — or, with
        ``None``, as ``withdrawal_for`` answers under the lock. Raises when the turn has already ended, so the
        routing writes beside it roll back."""
        ...


class _NoTurn:
    """A routing no turn owns — a refusal at submit, an actionless node — committing on its own."""

    def end(self, transaction: LockedTransaction, *, outcome: str, closure: str | None) -> None:
        return None


#: The end of no turn.
NO_TURN: ITurnEnd = _NoTurn()


class HubNodeRouter:
    """Routes what a hub step answered, in the transaction that ends its turn."""

    def __init__(
        self,
        *,
        artifacts: IWriteChunkArtifactsRepository,
        hub_exec: IWriteChunkHubExecRepository,
        escalations: IWriteChunkEscalationsRepository,
        exclusive: IChunkExclusiveWrites,
        transactions: IHubTransactions,
        events: EventLogService,
        clock: IClock,
        repositories: IReadRepositoryRecordRepository,
        forge_records: IReadForgeRepository,
        links: IReadProjectLinks,
        targets: ChunkTargets,
        projects: IReadProjectRepository,
        work_sources: IWorkSourceRegistry,
        movement: IWriteChunkMovementRepository,
        decisions: IWriteChunkDecisionsRepository,
        graphs: IReadGraphRepository,
        facts: IReadChunkFactsRepository,
        follow_latest_default: bool,
    ) -> None:
        self._facts = facts
        self._follow_latest_default = follow_latest_default
        self._artifacts = artifacts
        self._hub_exec = hub_exec
        self._escalations = escalations
        self._exclusive = exclusive
        self._transactions = transactions
        self._events = events
        self._clock = clock
        self._repositories = repositories
        self._forge_records = forge_records
        self._links = links
        self._targets = targets
        self._projects = projects
        self._work_sources = work_sources
        self._movement = movement
        self._decisions = decisions
        self._graphs = graphs

    def route(
        self,
        chunk: Chunk,
        graph: Graph,
        node: Node,
        *,
        epoch: int,
        choice: str,
        ending: ITurnEnd = NO_TURN,
        commits: list[dict[str, str]] | None = None,
        action: BouncedAction | None = None,
    ) -> None:
        """Route ``choice`` along its authored edge at ``epoch + 1``; a choice no edge routes, or one routed to a node
        the graph lacks, escalates. A delivery the markers say is incomplete bounces first, escalating past the
        node's bounce cap."""
        destination = self._destination(graph, node, choice)
        if not isinstance(destination, _Transition):
            self._route_away(chunk, node, destination, epoch=epoch, choice=choice, ending=ending)
            return
        envelope: str | None = None
        # A delivery kick-back, detected from the landed-marker fact and never from the choice name.
        if commits and destination.to_node_id != RESERVED_TERMINAL:
            pending_repos = {c["repo"] for c in commits}
            artifacts = self._artifacts.load_artifacts(chunk.chunk_id)
            finished = finished_repos(artifacts, artifacts, commits)
            if not pending_repos.issubset(finished):
                envelope = BounceEnvelope.of(
                    cause=choice,
                    summary=(
                        f"hub node `{node.name}` routed `{choice}` to `{destination.to_node_name}` — "
                        "delivery incomplete"
                    ),
                    commits=commits,
                    sources=self._delivery_sources(chunk),
                    action=action,
                ).serialize()
        self._commit(
            graph, chunk, node, destination, epoch=epoch, choice=choice, bounce=(choice, envelope), ending=ending
        )

    def _destination(self, graph: Graph, node: Node, choice: str) -> _Destination:
        """Where ``choice`` routes from ``node``, resolved before any lock is taken: a cross-graph edge's target is
        another aggregate's, read here."""
        edge = graph.edge_for_choice(node.node_id, choice)
        if edge is not None and edge.target_graph is not None:
            target = self._graphs.get_enabled_by_name(edge.target_graph)
            if target is None:
                unresolvable = UnresolvableTarget.of(edge)
                return _Escalate(ActionEscalation(unresolvable.cause, unresolvable.detail))
            return _Migrate(Landing.authored(edge, target, node))
        to_node_id = _target(graph, edge.to_node_name) if edge is not None else None
        if edge is None or to_node_id is None:
            return _Unroutable(edge.to_node_name if edge is not None else None)
        return _Transition(to_node_id, edge.to_node_name, self._drift(graph, to_node_id))

    def _drift(self, graph: Graph, to_node_id: str) -> _Drift | None:
        """The follow-latest inputs for a transition into a node — read here, before the lock, as an authored target
        is. ``None`` for the reserved terminal, a policy that is off, or no strictly newer enabled mint."""
        if to_node_id == RESERVED_TERMINAL:
            return None
        policy = FollowLatest.of(self._graphs.follow_latest(graph.graph_id), hub_default=self._follow_latest_default)
        if not policy.enabled:
            return None
        newest = self._graphs.get_enabled_by_name(graph.name)
        if newest is None or policy.target(graph, newest) is None:
            return None
        return _Drift(policy, newest)

    def _route_away(
        self,
        chunk: Chunk,
        node: Node,
        destination: _Migrate | _Escalate | _Unroutable,
        *,
        epoch: int,
        choice: str,
        ending: ITurnEnd,
    ) -> None:
        """A choice that does not transition, each in its own transaction under the chunk's lock: a cross-graph
        migration, which records the migration onto the target graph, opens the landed node's gate, and ends the
        turn; an escalation; or an answer no edge routes."""
        if isinstance(destination, _Escalate):
            self.escalate(chunk, destination.escalation, epoch=epoch, ending=ending)
            return
        close_decision = self._close_decision(chunk)
        if isinstance(destination, _Unroutable):
            self._unroutable(
                chunk,
                node,
                epoch=epoch,
                choice=choice,
                to_node=destination.to_node,
                close=close_decision,
                ending=ending,
            )
            return
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            logged = self._migrate_on(
                handle,
                chunk,
                node,
                destination.landing,
                epoch=epoch,
                choice=choice,
                close=close_decision,
                ending=ending,
            )
        self._events.publish(*logged)

    def _commit(
        self,
        graph: Graph,
        chunk: Chunk,
        node: Node,
        destination: _Transition,
        *,
        epoch: int,
        choice: str,
        bounce: tuple[str, str | None],
        ending: ITurnEnd,
    ) -> None:
        """One transaction: the bounce ``(cause, envelope)`` when there is an envelope — escalating past the cap —
        else beside the transition its envelope rides on, and the turn's end."""
        close_decision = self._close_decision(chunk)
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            logged = self._commit_on(
                handle,
                graph,
                chunk,
                node,
                destination,
                epoch=epoch,
                choice=choice,
                bounce=bounce,
                close=close_decision,
                ending=ending,
            )
        self._events.publish(*logged)

    def _commit_on(
        self,
        handle: ILockedChunkRead,
        graph: Graph,
        chunk: Chunk,
        node: Node,
        destination: _Transition,
        *,
        epoch: int,
        choice: str,
        bounce: tuple[str, str | None],
        close: CloseEnqueueDecision,
        ending: ITurnEnd,
    ) -> list[LoggedEvent]:
        """The transition on ``handle`` — or the bounce, or the migration a drift routes it into — and the turn's
        end; the close-skip events it wrote, to publish once the transaction commits."""
        hub_epoch = epoch + 1
        cause, envelope = bounce
        artifacts: list[StoredArtifact] = []
        landing: Landing | None = None
        if destination.drift is not None:
            # The decision re-establishes its read (`bzh:write-reestablishes-read`): a visit no longer standing, or a
            # chunk no longer delivering, routes nothing.
            locked = handle.record(chunk.chunk_id)
            facts = handle.facts(chunk.chunk_id)
            if locked is None or facts is None or not facts.delivering_at(node.node_id, epoch=epoch):
                ending.end(handle.transaction, outcome=choice, closure=None)
                return []
            edge = graph.edge_for_choice(node.node_id, choice)
            assert edge is not None, f"a transition destination resolved from an edge of `{choice}` on `{node.name}`"
            landing = Landing.consult(
                locked,
                edge,
                MigrationTargets.of(
                    locked,
                    graph,
                    cross_graph=None,
                    intent_graph=None,
                    intent_retired=False,
                    follow_latest=destination.drift.policy,
                    newest_same_name=destination.drift.newest,
                ),
            )
        if envelope is not None:
            if self._bounce(handle, chunk, node, hub_epoch=hub_epoch, cause=cause, envelope=envelope):
                # The envelope rides on the escalation too: it is what marks this node's exit at the epoch, so a
                # replay of the crossing is recognised by `record_bounce`'s guard and counts nothing twice.
                written = self._artifacts.record_hub_artifact(
                    chunk.chunk_id,
                    node_id=node.node_id,
                    node_name=node.name,
                    epoch=hub_epoch,
                    admission=EpochAdmission.AT_OR_ABOVE,
                    terminal=terminal_under_lock(self._facts, chunk.chunk_id, transaction=handle.transaction),
                    name="bounce-envelope",
                    content=envelope,
                    at=self._clock.now(),
                    close_decision=close,
                    transaction=handle.transaction,
                )
                ending.end(handle.transaction, outcome=TURN_ESCALATED, closure=None)
                return list(close_skipped_events(written.close_skipped, chunk=chunk))
            artifacts.append(self._envelope_artifact(chunk, node, hub_epoch=hub_epoch, envelope=envelope))
        if landing is not None:
            return self._migrate_on(
                handle,
                chunk,
                node,
                landing,
                epoch=epoch,
                choice=choice,
                close=close,
                ending=ending,
                artifacts=artifacts,
            )
        written = self._hub_exec.record_hub_step_transition(
            chunk.chunk_id,
            from_node_id=node.node_id,
            to_node_id=destination.to_node_id,
            choice_name=choice,
            epoch=hub_epoch,
            admission=EpochAdmission.ABOVE,
            terminal=terminal_under_lock(self._facts, chunk.chunk_id, transaction=handle.transaction),
            runner_id=_HUB_RUNNER_ID,
            transition_id=Id.mint(IdPrefix.TRANSITION, self._clock).value,
            at=self._clock.now(),
            artifacts=artifacts,
            release_route=destination.to_node_id == RESERVED_TERMINAL,
            close_decision=close,
            transaction=handle.transaction,
        )
        close_skipped = written.close_skipped
        if written.wrote:
            close_skipped += open_graph_gate_on_arrival(
                self._decisions,
                self._facts,
                handle.transaction,
                self._clock,
                chunk_id=chunk.chunk_id,
                graph=graph,
                node_id=destination.to_node_id,
                epoch=hub_epoch,
                close_decision=close,
            )
        ending.end(handle.transaction, outcome=choice, closure=choice if written.wrote else None)
        return list(close_skipped_events(close_skipped, chunk=chunk))

    def _migrate_on(
        self,
        handle: ILockedChunkRead,
        chunk: Chunk,
        node: Node,
        landing: Landing,
        *,
        epoch: int,
        choice: str,
        close: CloseEnqueueDecision,
        ending: ITurnEnd,
        artifacts: list[StoredArtifact] | None = None,
    ) -> list[LoggedEvent]:
        """The migration onto ``landing`` at the visit's own ``epoch`` — it mints none — a standing intent staying
        parked, and the landed node's gate opened. ``artifacts`` ride on the migration's own transaction. Answers the
        close-skip events it wrote, to publish once the transaction commits."""
        outcome = self._movement.record_migration(
            chunk.chunk_id,
            from_node_id=node.node_id,
            from_graph_id=node.graph_id,
            to_graph_id=landing.graph.graph_id,
            landed_node_id=landing.node_id,
            choice_name=choice,
            decision_id=None,
            model=landing.model,
            epoch=epoch,
            admission=EpochAdmission.CURRENT,
            terminal=terminal_under_lock(self._facts, chunk.chunk_id, transaction=handle.transaction),
            claimant=None,
            at=self._clock.now(),
            artifacts=artifacts or [],
            source=landing.source,
            release_route=landing.releases_route,
            clear_intent=landing.clear_intent,
            migration_id=Id.mint(IdPrefix.MIGRATION, self._clock).value,
            close_decision=close,
            transaction=handle.transaction,
        )
        close_skipped: tuple[int, ...] = ()
        wrote = isinstance(outcome, MigrationWrite)
        if isinstance(outcome, MigrationWrite):
            close_skipped = outcome.close_skipped + open_graph_gate_on_arrival(
                self._decisions,
                self._facts,
                handle.transaction,
                self._clock,
                chunk_id=chunk.chunk_id,
                graph=landing.graph,
                node_id=landing.node_id,
                epoch=epoch,
                close_decision=close,
            )
        ending.end(handle.transaction, outcome=choice, closure=choice if wrote else None)
        return list(close_skipped_events(close_skipped, chunk=chunk))

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
        ending: ITurnEnd = NO_TURN,
    ) -> None:
        """Record why the visit cannot run — its ``hub-log.<step>`` artifact, and the refusal's event the first time
        — and route its failure choice, before any pass is taken. One transaction under the chunk's lock writes them
        all with the turn's end, deciding from the chunk's facts and the visit's artifacts re-read there
        (``bzh:write-reestablishes-read``): a chunk no longer delivering at the visit writes none of them, and only
        the turn's end is written. The event is published once the transaction commits
        (``bzh:publish-after-commit``)."""
        choice = HUB_DEFAULT_FAILURE_CHOICE
        destination = self._destination(graph, node, choice)
        close_decision = self._close_decision(chunk)
        name = f"{HUB_LOG_PREFIX}{step}"
        logged: list[LoggedEvent] = []
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            visit = self._standing_visit(handle, chunk, node, epoch=epoch)
            if visit is None:
                ending.end(handle.transaction, outcome=destination.turn_outcome(choice), closure=None)
                return
            now = self._clock.now()
            written = self._artifacts.record_hub_artifact(
                chunk.chunk_id,
                node_id=node.node_id,
                node_name=node.name,
                epoch=epoch,
                admission=EpochAdmission.AT_OR_ABOVE,
                terminal=terminal_under_lock(self._facts, chunk.chunk_id, transaction=handle.transaction),
                name=name,
                content=f"[{step.replace('-', ' ')}]\n{detail}\n",
                at=now,
                close_decision=close_decision,
                transaction=handle.transaction,
            )
            logged.extend(close_skipped_events(written.close_skipped, chunk=chunk))
            if resolution is not None and (kind := REFUSAL_EVENT[resolution.outcome]) and visit.first(name):
                logged.append(
                    self._events.record(
                        kind=kind,
                        runner_id=None,
                        chunk_id=chunk.chunk_id,
                        project_id=chunk.project_id,
                        lease_id=None,
                        node_name=node.name,
                        message=resolution.detail,
                        detail={"epoch": epoch},
                        at=now,
                        transaction=handle.transaction,
                    )
                )
            logged.extend(
                self._route_on(
                    handle,
                    visit,
                    graph,
                    chunk,
                    node,
                    destination,
                    epoch=epoch,
                    choice=choice,
                    close=close_decision,
                    ending=ending,
                )
            )
        self._events.publish(*logged)

    def _route_on(
        self,
        handle: ILockedChunkRead,
        visit: _StandingVisit,
        graph: Graph,
        chunk: Chunk,
        node: Node,
        destination: _Destination,
        *,
        epoch: int,
        choice: str,
        close: CloseEnqueueDecision,
        ending: ITurnEnd,
    ) -> list[LoggedEvent]:
        """Route ``choice`` to ``destination`` on ``handle``, as :meth:`route` routes a choice with no delivery to
        bounce — the events it wrote, to publish once the transaction commits."""
        if isinstance(destination, _Migrate):
            return self._migrate_on(
                handle, chunk, node, destination.landing, epoch=epoch, choice=choice, close=close, ending=ending
            )
        if isinstance(destination, _Escalate):
            self._escalated_on(handle, chunk, destination.escalation, epoch=epoch, ending=ending)
            return []
        if isinstance(destination, _Unroutable):
            return self._unroutable_on(
                handle,
                visit,
                chunk,
                node,
                epoch=epoch,
                choice=choice,
                to_node=destination.to_node,
                close=close,
                ending=ending,
            )
        return self._commit_on(
            handle,
            graph,
            chunk,
            node,
            destination,
            epoch=epoch,
            choice=choice,
            bounce=(choice, None),
            close=close,
            ending=ending,
        )

    def _standing_visit(
        self, handle: ILockedChunkRead, chunk: Chunk, node: Node, *, epoch: int
    ) -> _StandingVisit | None:
        """The visit at ``node`` and ``epoch`` with its artifacts, read on ``handle`` — ``None`` once the chunk is no
        longer delivering at it (:meth:`ChunkFacts.delivering_at`)."""
        facts = handle.facts(chunk.chunk_id)
        if facts is None or not facts.delivering_at(node.node_id, epoch=epoch):
            return None
        artifacts = self._artifacts.load_artifacts(chunk.chunk_id, transaction=handle.transaction)
        return _StandingVisit(node_id=node.node_id, epoch=epoch, artifacts=tuple(artifacts))

    def record_wait(
        self,
        chunk: Chunk,
        node: Node,
        *,
        epoch: int,
        kind: WaitKind,
        next_poll_at: datetime | None = None,
        ending: ITurnEnd = NO_TURN,
        reached: Callable[[], None] | None = None,
    ) -> None:
        """Record one reserved-wait poll row of ``kind`` for the visit — no transition, no retry or bounce spent. A
        pass's ``next_poll_at`` rides on the row and only ever postpones the next poll. ``reached`` marks the window
        between the row and the turn's end."""
        now = self._clock.now()
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            self._hub_exec.record_hub_node_poll(
                chunk.chunk_id,
                node_id=node.node_id,
                epoch=epoch,
                kind=kind,
                at=now,
                next_poll_at=next_poll_at,
                transaction=handle.transaction,
            )
            if reached is not None:
                reached()
            ending.end(handle.transaction, outcome=kind, closure=None)

    def route_pending_timeout(
        self, chunk: Chunk, graph: Graph, node: Node, *, epoch: int, ending: ITurnEnd = NO_TURN
    ) -> None:
        """A pending node past its ``poll_timeout`` is a kick-back, not a plain failure: a bounce, an escalation past
        the node's ``bounce_cap``, else the ``failure`` edge with the kick-back envelope riding along."""
        choice = HUB_DEFAULT_FAILURE_CHOICE
        destination = self._destination(graph, node, choice)
        if not isinstance(destination, _Transition):
            self._route_away(chunk, node, destination, epoch=epoch, choice=choice, ending=ending)
            return
        rows = GitCommits.of(self._artifacts.load_artifacts(chunk.chunk_id))
        project = read_landing_project(chunk.project_id, projects=self._projects, links=self._links)
        resolution = resolve_chunk_repositories(
            rows.origins(),
            self._repositories.list_all(include_retired=True),
            self._forge_records.list_all(include_retired=True),
            minted_at=chunk.minted_at,
            linked=project.linked,
            branches=branches(self._targets.effective(TargetChunk(chunk.chunk_id, chunk.project_id))),
            project=project.slug,
        )
        envelope = BounceEnvelope.of(
            cause="poll-timeout",
            summary=f"hub node `{node.name}` exceeded its poll_timeout awaiting `{_PENDING}`",
            commits=[{k: v or "" for k, v in c.items()} for c in rows.payload(resolution.qualified, resolution)],
            sources=self._delivery_sources(chunk),
            action=None,
        ).serialize()
        self._commit(
            graph,
            chunk,
            node,
            destination,
            epoch=epoch,
            choice=choice,
            bounce=("poll-timeout", envelope),
            ending=ending,
        )

    def escalate(self, chunk: Chunk, escalation: ActionEscalation, *, epoch: int, ending: ITurnEnd = NO_TURN) -> None:
        """An escalation the hub authors at the visit's epoch plus one, routing nothing."""
        self._escalated(chunk, escalation, epoch=epoch, ending=ending)

    def escalate_forge_auth(
        self,
        chunk: Chunk,
        node: Node,
        escalation: ActionEscalation,
        reason: ForgeAuthReason,
        *,
        epoch: int,
        ending: ITurnEnd = NO_TURN,
    ) -> bool:
        """Escalate a pass whose forge refused its credential, recording ``forge-auth-refused`` once the escalation
        takes — so a duplicate escalation of the same visit records none."""
        outcome = self._escalated(chunk, escalation, epoch=epoch, ending=ending)
        if outcome is not BounceEscalationOutcome.RECORDED:
            return False
        self._events.record(
            kind=_EVENT_FORGE_AUTH_REFUSED,
            runner_id=None,
            chunk_id=chunk.chunk_id,
            project_id=chunk.project_id,
            lease_id=None,
            node_name=node.name,
            message=escalation.detail,
            detail=forge_auth_refused_detail(
                reason.classified, forge_id=reason.forge, chunk_id=chunk.chunk_id, repository_id=reason.repository
            ),
            at=self._clock.now(),
        )
        return True

    def escalate_action_missing(self, chunk: Chunk, node: Node, *, epoch: int) -> bool:
        """A hub node naming no action — one the carry-over could not translate — escalates its chunk, its stored
        command named, recording ``hub-action-missing`` once the escalation takes."""
        stored = f"its stored run: {node.legacy_run}" if node.legacy_run else "no `run:`"
        detail = f"hub node `{node.name}` names no action ({stored})"
        outcome = self._escalated(
            chunk, ActionEscalation(EscalationCause.HUB_ACTION_MISSING, detail), epoch=epoch, ending=NO_TURN
        )
        if outcome is not BounceEscalationOutcome.RECORDED:
            return False
        self._events.record(
            kind=_EVENT_ACTION_MISSING,
            runner_id=None,
            chunk_id=chunk.chunk_id,
            project_id=chunk.project_id,
            lease_id=None,
            node_name=node.name,
            message=detail,
            detail={"epoch": epoch, "legacy_run": node.legacy_run},
            at=self._clock.now(),
        )
        return True

    def escalate_capability_missing(
        self,
        chunk: Chunk,
        node: Node,
        target: ResolvedRepository,
        missing: MissingCapability,
        explanation: str,
        *,
        forge: ConfiguredForge,
        epoch: int,
        ending: ITurnEnd = NO_TURN,
    ) -> None:
        """Escalate a visit whose forge lacks what the action needs, before its pass or mid-pass: only an operator
        can change what a forge offers or what an action asks of it. ``explanation`` is the caller's human account.
        Its ``hub-log`` artifact is written first, once per visit; its event only when the escalation takes."""
        detail = explanation
        close_decision = self._close_decision(chunk)
        with self._transactions.begin() as unit:
            locked = unit.lock_chunks([chunk.chunk_id])
            written = self._artifacts.record_hub_artifact(
                chunk.chunk_id,
                node_id=node.node_id,
                node_name=node.name,
                epoch=epoch,
                admission=EpochAdmission.AT_OR_ABOVE,
                terminal=terminal_under_lock(self._facts, chunk.chunk_id, transaction=locked),
                name=f"{HUB_LOG_PREFIX}{_CAPABILITY_MISSING_STEP}",
                content=f"[{_CAPABILITY_MISSING_STEP.replace('-', ' ')}]\n{detail}\n",
                at=self._clock.now(),
                close_decision=close_decision,
                transaction=locked,
            )
        self._events.publish(*close_skipped_events(written.close_skipped, chunk=chunk))
        escalation = ActionEscalation(EscalationCause.FORGE_CAPABILITY_MISSING, detail, missing)
        outcome = self._escalated(chunk, escalation, epoch=epoch, ending=ending)
        if outcome is BounceEscalationOutcome.RECORDED:
            self._events.record(
                kind=_EVENT_CAPABILITY_MISSING,
                runner_id=None,
                chunk_id=chunk.chunk_id,
                project_id=chunk.project_id,
                lease_id=None,
                node_name=node.name,
                message=detail,
                detail={
                    "epoch": epoch,
                    "capability": str(missing.first),
                    "repository": target.record,
                    "forge": forge.fields.api_base,
                },
                at=self._clock.now(),
            )

    def _escalated(
        self, chunk: Chunk, escalation: ActionEscalation, *, epoch: int, ending: ITurnEnd
    ) -> BounceEscalationOutcome:
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            return self._escalated_on(handle, chunk, escalation, epoch=epoch, ending=ending)

    def _escalated_on(
        self, handle: ILockedChunkRead, chunk: Chunk, escalation: ActionEscalation, *, epoch: int, ending: ITurnEnd
    ) -> BounceEscalationOutcome:
        outcome = self._escalate_locked(handle, chunk, escalation.cause, escalation.detail, epoch=epoch + 1)
        ending.end(handle.transaction, outcome=TURN_ESCALATED, closure=None)
        return outcome

    def _escalate_locked(
        self, handle: ILockedChunkRead, chunk: Chunk, cause: EscalationCause, detail: str, *, epoch: int
    ) -> BounceEscalationOutcome:
        """Written as a bounce-cap escalation is — at ``epoch``, the visit's plus one, beside the hub's lease."""
        return self._escalations.record_bounce_escalation(
            chunk.chunk_id,
            epoch=epoch,
            admission=EpochAdmission.ABOVE,
            terminal=terminal_under_lock(self._facts, chunk.chunk_id, transaction=handle.transaction),
            runner_id=_HUB_RUNNER_ID,
            takeover_command="",
            at=self._clock.now(),
            cause=cause,
            detail=detail,
            transaction=handle.transaction,
        )

    def _bounce(
        self, handle: ILockedChunkRead, chunk: Chunk, node: Node, *, hub_epoch: int, cause: str, envelope: str
    ) -> bool:
        """Record a bounce, and escalate once it crosses the node's cap — whether it crossed."""
        self._escalations.record_bounce(
            chunk.chunk_id,
            epoch=hub_epoch,
            admission=EpochAdmission.ABOVE,
            terminal=terminal_under_lock(self._facts, chunk.chunk_id, transaction=handle.transaction),
            cause=cause,
            envelope=envelope,
            at=self._clock.now(),
            visit_node_id=node.node_id,
            transaction=handle.transaction,
        )
        facts = handle.facts(chunk.chunk_id)
        cap = node.bounce_cap if node.bounce_cap is not None else DEFAULT_BOUNCE_CAP
        if facts is None or not facts.bounces_over_cap(cap):
            return False
        crossed = f"bounce cap ({cap}) crossed after {facts.bounce_count()} bounces"
        self._escalate_locked(handle, chunk, EscalationCause.BOUNCE_CAP, crossed, epoch=hub_epoch)
        return True

    def _envelope_artifact(self, chunk: Chunk, node: Node, *, hub_epoch: int, envelope: str) -> StoredArtifact:
        return StoredArtifact(
            kind=ArtifactKind.ASSET,
            name="bounce-envelope",
            data=envelope,
            repo=None,
            forge=None,
            artifact_id=Id.mint(IdPrefix.ARTIFACT, self._clock).value,
            chunk_id=chunk.chunk_id,
            node_id=node.node_id,
            node_name=node.name,
            epoch=hub_epoch,
        )

    def _unroutable(
        self,
        chunk: Chunk,
        node: Node,
        *,
        epoch: int,
        choice: str,
        to_node: str | None,
        close: CloseEnqueueDecision,
        ending: ITurnEnd,
    ) -> None:
        """An answer no edge routes is a fault in the graph: announce it once per visit, and escalate the chunk — one
        transaction under the chunk's lock, deciding from the facts and artifacts re-read there, as :meth:`refuse`
        does. Its event is published once the transaction commits."""
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            visit = self._standing_visit(handle, chunk, node, epoch=epoch)
            if visit is None:
                ending.end(handle.transaction, outcome=_Unroutable(to_node).turn_outcome(choice), closure=None)
                return
            logged = self._unroutable_on(
                handle, visit, chunk, node, epoch=epoch, choice=choice, to_node=to_node, close=close, ending=ending
            )
        self._events.publish(*logged)

    def _unroutable_on(
        self,
        handle: ILockedChunkRead,
        visit: _StandingVisit,
        chunk: Chunk,
        node: Node,
        *,
        epoch: int,
        choice: str,
        to_node: str | None,
        close: CloseEnqueueDecision,
        ending: ITurnEnd,
    ) -> list[LoggedEvent]:
        now = self._clock.now()
        if to_node is None:
            detail = f"no authored edge for choice `{choice}` on hub node `{node.name}`"
        else:
            detail = f"choice `{choice}` on hub node `{node.name}` routes to unknown node `{to_node}`"
        authored = sorted(c.name for c in node.choices)
        written = self._artifacts.record_hub_artifact(
            chunk.chunk_id,
            node_id=node.node_id,
            node_name=node.name,
            epoch=epoch,
            admission=EpochAdmission.AT_OR_ABOVE,
            terminal=terminal_under_lock(self._facts, chunk.chunk_id, transaction=handle.transaction),
            name=_UNROUTABLE_ARTIFACT_NAME,
            content=(
                f"{detail}\n\n"
                f"authored choices: {', '.join(authored) or '<none>'}\n"
                "The chunk is escalated to a person; nothing routes it on.\n"
            ),
            at=now,
            close_decision=close,
            transaction=handle.transaction,
        )
        logged = list(close_skipped_events(written.close_skipped, chunk=chunk))
        if visit.first(_UNROUTABLE_ARTIFACT_NAME):
            logged.append(
                self._events.record(
                    kind=_EVENT_UNROUTABLE_OUTCOME,
                    runner_id=None,
                    chunk_id=chunk.chunk_id,
                    project_id=chunk.project_id,
                    lease_id=None,
                    node_name=node.name,
                    message=detail,
                    detail={"choice": choice, "epoch": epoch, "authored_choices": authored},
                    at=now,
                    transaction=handle.transaction,
                )
            )
        named = f"`{choice}`, which no edge routes" if to_node is None else f"`{choice}`, routed to unknown `{to_node}`"
        unroutable = ActionEscalation(EscalationCause.UNROUTABLE_OUTCOME, f"hub node `{node.name}` answered {named}")
        self._escalated_on(handle, chunk, unroutable, epoch=epoch, ending=ending)
        return logged

    def _close_decision(self, chunk: Chunk) -> CloseEnqueueDecision:
        return decide_close_enqueue(chunk.work_refs, lambda source: self._work_sources.closer(source) is not None)

    def _delivery_sources(self, chunk: Chunk) -> DeliverySources:
        """The chunk's delivery markers, the PR references already parsed by the store. The historical landing
        facts are left out: a bounce reads only what the markers say landed."""
        read = self._artifacts.delivery_sources_for([chunk.chunk_id]).get(chunk.chunk_id, DeliverySources())
        return DeliverySources(markers=read.markers, pr_markers=read.pr_markers)


def _target(graph: Graph, to_node_name: str) -> str | None:
    """The node id an edge routes to — the reserved terminal, or ``None`` for a node the graph lacks."""
    if to_node_name == RESERVED_TERMINAL:
        return RESERVED_TERMINAL
    target = graph.node_by_name(to_node_name)
    return target.node_id if target is not None else None


_refusals_conforms: type[IHubStepRefusals] = HubNodeRouter
