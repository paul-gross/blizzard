"""Where a hub step's answer goes — the routing a turn's end writes (``bzh:deterministic-shell``).

:class:`HubNodeRouter` maps an answer to an authored edge, records a wait, bounces an incomplete delivery, and
escalates to a person what no edge routes. Every chunk write it makes for a turn goes in one locked transaction with
the turn's end, which the :class:`ITurnEnd` it is handed writes; with no turn — a refusal at submit, an actionless
node — the same writes commit alone."""

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
from blizzard.hub.domain.actions.effects import ActionEscalation
from blizzard.hub.domain.actions.visit import GitCommits, LandingProject
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.delivery_read import DeliveryRead, DeliverySources
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, LandedRepos, WaitKind, is_landed_revision
from blizzard.hub.domain.chunk.no_code import no_code_repos
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.domain.chunk.ports.escalations import BounceEscalationOutcome, IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites, ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.hub_exec import IWriteChunkHubExecRepository
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.forges import ConfiguredForge, IReadForgeRepository
from blizzard.hub.domain.config.repositories import (
    IReadRepositoryRecordRepository,
    RepositoryOutcome,
    RepositoryResolution,
    ResolvedRepository,
    resolve_chunk_repositories,
)
from blizzard.hub.domain.delivery.flows import TURN_ESCALATED
from blizzard.hub.domain.delivery.ports.flows import IHubStepRefusals
from blizzard.hub.domain.graph.actions import RESERVED_WAITS
from blizzard.hub.domain.graph.model import (
    DEFAULT_BOUNCE_CAP,
    HUB_DEFAULT_FAILURE_CHOICE,
    RESERVED_TERMINAL,
    Graph,
    Node,
)
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.domain.projects.model import IReadProjectRepository

_HUB_RUNNER_ID = "hub"
# Once-per-(node, epoch) dedupe key for an outcome with no authored edge.
_UNROUTABLE_ARTIFACT_NAME = "hub-unroutable-outcome"
_EVENT_UNROUTABLE_OUTCOME: EventLogKind = "hub-node-unroutable-outcome"
_EVENT_ACTION_MISSING: EventLogKind = "hub-action-missing"
_EVENT_CAPABILITY_MISSING: EventLogKind = "forge-capability-missing"
_CAPABILITY_MISSING_STEP = "forge-capability-missing"
HUB_LOG_PREFIX = "hub-log."
# The one reserved wait a pass answers; the router never authors it.
(_PENDING,) = RESERVED_WAITS
_MARKER_PREFIX = "merged/"
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
        landed = LandedRepos.of(sources.markers).names | no_code_repos(sources.markers, commits)
        read = DeliveryRead.of(ChunkFacts(minted=True), sources)
        prs = {p.repo: p for p in sorted([*read.closed_prs, *read.open_prs], key=lambda p: p.number)}
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
                "step": None,
                "action": self.action.payload() if self.action else None,
            },
            ensure_ascii=False,
        )


class ITurnEnd(Protocol):
    """The end of the turn a routing belongs to, written on the routing's own locked transaction."""

    def end(self, handle: ILockedChunkRead, *, outcome: str, closure: str | None) -> None:
        """End the turn ``outcome`` on ``handle``, closing its submission ``closure`` — a verdict — or, with
        ``None``, as ``withdrawal_for`` answers under the lock. Raises when the turn has already ended, so the
        routing writes beside it roll back."""
        ...


class _NoTurn:
    """A routing no turn owns — a refusal at submit, an actionless node — committing on its own."""

    def end(self, handle: ILockedChunkRead, *, outcome: str, closure: str | None) -> None:
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
        events: EventLogService,
        clock: IClock,
        repositories: IReadRepositoryRecordRepository,
        forge_records: IReadForgeRepository,
        links: IReadProjectLinks,
        projects: IReadProjectRepository,
    ) -> None:
        self._artifacts = artifacts
        self._hub_exec = hub_exec
        self._escalations = escalations
        self._exclusive = exclusive
        self._events = events
        self._clock = clock
        self._repositories = repositories
        self._forge_records = forge_records
        self._links = links
        self._projects = projects

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
        edge = graph.edge_for_choice(node.node_id, choice)
        to_node_id = _target(graph, edge.to_node_name) if edge is not None else None
        if edge is None or to_node_id is None:
            self._unroutable(
                chunk, node, epoch=epoch, choice=choice, to_node=edge.to_node_name if edge else None, ending=ending
            )
            return
        envelope: str | None = None
        # A delivery kick-back, detected from the landed-marker fact and never from the choice name.
        if commits and to_node_id != RESERVED_TERMINAL:
            pending_repos = {c["repo"] for c in commits}
            artifacts = self._artifacts.load_artifacts(chunk.chunk_id)
            finished = LandedRepos.of(artifacts).names | no_code_repos(artifacts, commits)
            if not pending_repos.issubset(finished):
                envelope = BounceEnvelope.of(
                    cause=choice,
                    summary=f"hub node `{node.name}` routed `{choice}` to `{edge.to_node_name}` — delivery incomplete",
                    commits=commits,
                    sources=self._delivery_sources(chunk),
                    action=action,
                ).serialize()
        self._commit(
            chunk, node, epoch=epoch, choice=choice, to_node_id=to_node_id, bounce=(choice, envelope), ending=ending
        )

    def _commit(
        self,
        chunk: Chunk,
        node: Node,
        *,
        epoch: int,
        choice: str,
        to_node_id: str,
        bounce: tuple[str, str | None],
        ending: ITurnEnd,
    ) -> None:
        """One transaction: the bounce ``(cause, envelope)`` when there is an envelope — escalating past the cap —
        else beside the transition its envelope rides on, and the turn's end."""
        hub_epoch = epoch + 1
        cause, envelope = bounce
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            artifacts: list[StoredArtifact] = []
            if envelope is not None:
                if self._bounce(handle, chunk, node, hub_epoch=hub_epoch, cause=cause, envelope=envelope):
                    ending.end(handle, outcome=TURN_ESCALATED, closure=None)
                    return
                artifacts.append(self._envelope_artifact(chunk, node, hub_epoch=hub_epoch, envelope=envelope))
            wrote = self._hub_exec.record_hub_step_transition_locked(
                handle,
                chunk.chunk_id,
                from_node_id=node.node_id,
                to_node_id=to_node_id,
                choice_name=choice,
                epoch=hub_epoch,
                admission=EpochAdmission.ABOVE,
                runner_id=_HUB_RUNNER_ID,
                transition_id=Id.mint(IdPrefix.TRANSITION, self._clock).value,
                at=self._clock.now(),
                artifacts=artifacts,
                release_route=to_node_id == RESERVED_TERMINAL,
            )
            ending.end(handle, outcome=choice, closure=choice if wrote else None)

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
        — and route its failure choice, before any pass is taken."""
        announced = self._artifacts.record_hub_artifact(
            chunk.chunk_id,
            node_id=node.node_id,
            node_name=node.name,
            epoch=epoch,
            admission=EpochAdmission.AT_OR_ABOVE,
            name=f"{HUB_LOG_PREFIX}{step}",
            content=f"[{step.replace('-', ' ')}]\n{detail}\n",
            at=self._clock.now(),
        )
        if announced and resolution is not None:
            self._record_refusal_event(chunk, node, resolution, epoch=epoch)
        self.route(chunk, graph, node, epoch=epoch, choice=HUB_DEFAULT_FAILURE_CHOICE, commits=[], ending=ending)

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
            self._hub_exec.record_hub_node_poll_locked(
                handle, chunk.chunk_id, node_id=node.node_id, epoch=epoch, kind=kind, at=now, next_poll_at=next_poll_at
            )
            if reached is not None:
                reached()
            ending.end(handle, outcome=_PENDING, closure=None)

    def route_pending_timeout(
        self, chunk: Chunk, graph: Graph, node: Node, *, epoch: int, ending: ITurnEnd = NO_TURN
    ) -> None:
        """A pending node past its ``poll_timeout`` is a kick-back, not a plain failure: a bounce, an escalation past
        the node's ``bounce_cap``, else the ``failure`` edge with the kick-back envelope riding along."""
        choice = HUB_DEFAULT_FAILURE_CHOICE
        edge = graph.edge_for_choice(node.node_id, choice)
        to_node_id = _target(graph, edge.to_node_name) if edge is not None else None
        if edge is None or to_node_id is None:
            self._unroutable(
                chunk, node, epoch=epoch, choice=choice, to_node=edge.to_node_name if edge else None, ending=ending
            )
            return
        rows = GitCommits.of(self._artifacts.load_artifacts(chunk.chunk_id))
        project = LandingProject.read(chunk.project_id, projects=self._projects, links=self._links)
        resolution = resolve_chunk_repositories(
            rows.origins(),
            self._repositories.list_all(include_retired=True),
            self._forge_records.list_all(include_retired=True),
            minted_at=chunk.minted_at,
            linked=project.linked,
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
            chunk,
            node,
            epoch=epoch,
            choice=choice,
            to_node_id=to_node_id,
            bounce=("poll-timeout", envelope),
            ending=ending,
        )

    def escalate(self, chunk: Chunk, escalation: ActionEscalation, *, epoch: int, ending: ITurnEnd = NO_TURN) -> None:
        """An escalation the hub authors at the visit's epoch plus one, routing nothing."""
        self._escalated(chunk, escalation, epoch=epoch, ending=ending)

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
        action_name: str,
        target: ResolvedRepository,
        capability: Capability,
        *,
        forge: ConfiguredForge,
        epoch: int,
        ending: ITurnEnd = NO_TURN,
    ) -> None:
        """Refuse the visit before its pass: only an operator can change what a forge offers or what an action asks
        of it. Its ``hub-log`` artifact is written first, once per visit; its event only when the escalation takes."""
        detail = (
            f"repository {target.slug or target.record} on forge {forge.label} does not offer `{capability}`, "
            f"which `{action_name}` requires"
        )
        self._artifacts.record_hub_artifact(
            chunk.chunk_id,
            node_id=node.node_id,
            node_name=node.name,
            epoch=epoch,
            admission=EpochAdmission.AT_OR_ABOVE,
            name=f"{HUB_LOG_PREFIX}{_CAPABILITY_MISSING_STEP}",
            content=f"[{_CAPABILITY_MISSING_STEP.replace('-', ' ')}]\n{detail}\n",
            at=self._clock.now(),
        )
        escalation = ActionEscalation(EscalationCause.FORGE_CAPABILITY_MISSING, detail)
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
                    "capability": str(capability),
                    "repository": target.record,
                    "forge": forge.fields.api_base,
                },
                at=self._clock.now(),
            )

    def _escalated(
        self, chunk: Chunk, escalation: ActionEscalation, *, epoch: int, ending: ITurnEnd
    ) -> BounceEscalationOutcome:
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            outcome = self._escalate_locked(handle, chunk, escalation.cause, escalation.detail, epoch=epoch + 1)
            ending.end(handle, outcome=TURN_ESCALATED, closure=None)
        return outcome

    def _escalate_locked(
        self, handle: ILockedChunkRead, chunk: Chunk, cause: EscalationCause, detail: str, *, epoch: int
    ) -> BounceEscalationOutcome:
        """Written as a bounce-cap escalation is — at ``epoch``, the visit's plus one, beside the hub's lease."""
        return self._escalations.record_bounce_escalation_locked(
            handle,
            chunk.chunk_id,
            epoch=epoch,
            admission=EpochAdmission.ABOVE,
            runner_id=_HUB_RUNNER_ID,
            takeover_command="",
            at=self._clock.now(),
            cause=cause,
            detail=detail,
        )

    def _bounce(
        self, handle: ILockedChunkRead, chunk: Chunk, node: Node, *, hub_epoch: int, cause: str, envelope: str
    ) -> bool:
        """Record a bounce, and escalate once it crosses the node's cap — whether it crossed."""
        self._escalations.record_bounce_locked(
            handle,
            chunk.chunk_id,
            epoch=hub_epoch,
            admission=EpochAdmission.ABOVE,
            cause=cause,
            envelope=envelope,
            at=self._clock.now(),
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
        self, chunk: Chunk, node: Node, *, epoch: int, choice: str, to_node: str | None, ending: ITurnEnd
    ) -> None:
        """An answer no edge routes is a fault in the graph: announce it once per visit, and escalate the chunk."""
        now = self._clock.now()
        if to_node is None:
            detail = f"no authored edge for choice `{choice}` on hub node `{node.name}`"
        else:
            detail = f"choice `{choice}` on hub node `{node.name}` routes to unknown node `{to_node}`"
        authored = sorted(c.name for c in node.choices)
        announced = self._artifacts.record_hub_artifact(
            chunk.chunk_id,
            node_id=node.node_id,
            node_name=node.name,
            epoch=epoch,
            admission=EpochAdmission.AT_OR_ABOVE,
            name=_UNROUTABLE_ARTIFACT_NAME,
            content=(
                f"{detail}\n\n"
                f"authored choices: {', '.join(authored) or '<none>'}\n"
                "The chunk is escalated to a person; nothing routes it on.\n"
            ),
            at=now,
        )
        if announced:
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
            )
        named = f"`{choice}`, which no edge routes" if to_node is None else f"`{choice}`, routed to unknown `{to_node}`"
        unroutable = ActionEscalation(EscalationCause.UNROUTABLE_OUTCOME, f"hub node `{node.name}` answered {named}")
        self.escalate(chunk, unroutable, epoch=epoch, ending=ending)

    def _delivery_sources(self, chunk: Chunk) -> DeliverySources:
        """The chunk's delivery markers, the PR references already parsed by the store. The historical landing
        facts are left out: a bounce reads only what the markers say landed."""
        read = self._artifacts.delivery_sources_for([chunk.chunk_id]).get(chunk.chunk_id, DeliverySources())
        return DeliverySources(markers=read.markers, pr_markers=read.pr_markers)

    def _record_refusal_event(self, chunk: Chunk, node: Node, resolution: RepositoryResolution, *, epoch: int) -> None:
        kind = REFUSAL_EVENT[resolution.outcome]
        if kind is None:
            return
        self._events.record(
            kind=kind,
            runner_id=None,
            chunk_id=chunk.chunk_id,
            project_id=chunk.project_id,
            lease_id=None,
            node_name=node.name,
            message=resolution.detail,
            detail={"epoch": epoch},
            at=self._clock.now(),
        )


class HubMarkerWriter:
    """The deprecated marker route's writer: a marker a runner posts for a hub node's visit, written outside any
    turn."""

    def __init__(
        self, *, artifacts: IWriteChunkArtifactsRepository, delivery: IWriteChunkDeliveryRepository, clock: IClock
    ) -> None:
        self._artifacts = artifacts
        self._delivery = delivery
        self._clock = clock

    def record_marker(
        self, chunk_id: str, *, node_id: str, node_name: str, epoch: int, name: str, content: str
    ) -> bool:
        """Record a hub node visit's marker, idempotently per ``(chunk, node, name, epoch)``. A superseded epoch
        writes nothing (``bzh:epoch-fencing``). A landing it records names no repository record: only a pass's
        ``LandingRecord`` knows which record landed."""
        wrote = self._artifacts.record_hub_artifact(
            chunk_id,
            node_id=node_id,
            node_name=node_name,
            epoch=epoch,
            admission=EpochAdmission.AT_OR_ABOVE,
            name=name,
            content=content,
            at=self._clock.now(),
        )
        # Only a fresh marker counts as landed; replay must not double-count.
        if wrote and name.startswith(_MARKER_PREFIX) and is_landed_revision(content):
            self._delivery.record_delivery_repo_landed(
                chunk_id,
                repo=name.removeprefix(_MARKER_PREFIX),
                commit_hash=content,
                repository_id=None,
                at=self._clock.now(),
            )
        return wrote


def _target(graph: Graph, to_node_name: str) -> str | None:
    """The node id an edge routes to — the reserved terminal, or ``None`` for a node the graph lacks."""
    if to_node_name == RESERVED_TERMINAL:
        return RESERVED_TERMINAL
    target = graph.node_by_name(to_node_name)
    return target.node_id if target is not None else None


_refusals_conforms: type[IHubStepRefusals] = HubNodeRouter
