"""The generic hub command node executor — THE primitive (#65).

Take one pass of a hub node's action and map its answer to an authored edge; a node naming no
action, or an answer no edge routes, escalates its chunk to a person. Structurally agentless
(``bzh:deterministic-shell``); a pure step function over injected seams (``bzh:steppable-loop``,
``bzh:domain-core``). Serialization is a fact: ``run`` returns ``None`` if the slot is held."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.platform_tracing.attributes import annotate
from blizzard.foundation.platform_tracing.tracer import IPlatformTracer, NoopPlatformTracer
from blizzard.foundation.roles import domain_model
from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.trace_ids import DerivedContext, SpanRole, StepKey
from blizzard.hub.delivery.action_pass import ActionPassDriver, PassRun
from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.domain.actions.effects import (
    ActionEscalation,
    Effect,
    EffectResult,
    EffectStatus,
    LandingRecord,
    Marker,
    Pass,
    RecordGardenDelivery,
    RecordReviewFindings,
)
from blizzard.hub.domain.actions.requirements import ACTION_REQUIREMENTS, Requirement
from blizzard.hub.domain.actions.visit import (
    GitCommits,
    LandingProject,
    Visit,
    VisitChunk,
    VisitNode,
    VisitRefusal,
    resolve_visit,
)
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.delivery_read import DeliveryRead, DeliverySources
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkFacts,
    HubNodePollFact,
    LandedRepos,
    is_landed_revision,
)
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.domain.chunk.ports.escalations import BounceEscalationOutcome, IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.hub_exec import IWriteChunkHubExecRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.forges import ConfiguredForge, IReadForgeRepository
from blizzard.hub.domain.config.repositories import (
    IReadRepositoryRecordRepository,
    RepositoryId,
    RepositoryOutcome,
    RepositoryResolution,
    RepositorySecretUnavailable,
    ResolvedRepository,
    resolve_chunk_repositories,
)
from blizzard.hub.domain.delivery.flows import PollPolicy
from blizzard.hub.domain.garden.delivery.materialize import DeliveryOutcome
from blizzard.hub.domain.garden.delivery.service import GardenDeliveryRecorder
from blizzard.hub.domain.garden.delivery.validation import GardenDeliveryRejected
from blizzard.hub.domain.garden.review.materialize import ReviewFindingsOutcome
from blizzard.hub.domain.garden.review.service import ReviewFindingsRecorder
from blizzard.hub.domain.garden.review.validation import ReviewFindingsRejected
from blizzard.hub.domain.garden.run_context import IReadRunContextRepository
from blizzard.hub.domain.graph.actions import RESERVED_WAITS, ActionSpec
from blizzard.hub.domain.graph.model import (
    DEFAULT_BOUNCE_CAP,
    HUB_DEFAULT_FAILURE_CHOICE,
    RESERVED_TERMINAL,
    Graph,
    IReadManyGraphs,
    Node,
)
from blizzard.hub.domain.observability.tracing.platform import ACTION_CHOICE, ACTION_NAME, ACTION_PASS_SPAN
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.domain.projects.model import IReadProjectRepository
from blizzard.hub.forge.landing import RepositoryForge
from blizzard.hub.forge.registry import ForgeRegistry
from blizzard.hub.work_sources.source import IWorkSourceRegistry

_HUB_RUNNER_ID = "hub"
# Once-per-(node, epoch) dedupe key for an outcome with no authored edge.
_UNROUTABLE_ARTIFACT_NAME = "hub-unroutable-outcome"
_EVENT_UNROUTABLE_OUTCOME: EventLogKind = "hub-node-unroutable-outcome"
_EVENT_ACTION_OVERRAN: EventLogKind = "hub-action-overran"
_EVENT_ACTION_MISSING: EventLogKind = "hub-action-missing"
_EVENT_CAPABILITY_MISSING: EventLogKind = "forge-capability-missing"
_CAPABILITY_MISSING_STEP = "forge-capability-missing"
_HUB_LOG_PREFIX = "hub-log."
# The one reserved wait a pass answers; the executor never authors it.
(_PENDING,) = RESERVED_WAITS
# What a delivery the chunk has moved past answers its action with.
_FENCED_DETAIL = "not recorded: the chunk is terminal or has moved past this visit"
# A landing record is written however the fence answers; this names why it answers `fenced` anyway.
_LANDED_PAST_FENCE_DETAIL = "the chunk is terminal or has moved past this visit"

# Generous bound: reclaim only a slot abandoned without a matching release.
DEFAULT_SLOT_STALE_AFTER = timedelta(minutes=30)

# A kill leaves the slot live; the stale bound reclaims it, with pending derived from the poll fact.
_CP_HUBNODE_AFTER_POLL_BEFORE_SLOT_RELEASE = crashpoint(
    "hubnode.after-poll.before-slot-release",
    "the poll-attempt fact is durable; the tenant's slot is not yet released",
)
# Fires after marker and intents are durable in one transaction, before drain.
_CP_CLOSE_AFTER_ENQUEUE_BEFORE_DRAIN = crashpoint(
    "close.after-enqueue.before-drain", "a landing marker and its close intents are durable; no drain has run yet"
)
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
        landed = LandedRepos.of(sources.markers).names
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


def _escalation_detail(crossed: str, outcome: BounceEscalationOutcome) -> str:
    """A bounce-cap crossing's run detail: an escalation already recorded at this epoch reads as escalated."""
    match outcome:
        case BounceEscalationOutcome.RECORDED:
            return f"{crossed}, escalated"
        case BounceEscalationOutcome.ALREADY_RECORDED:
            return f"{crossed}, already escalated"
        case BounceEscalationOutcome.REFUSED:
            return f"{crossed}, escalation not recorded (chunk moved on)"


@domain_model
@dataclass(frozen=True)
class HubRunResult:
    """The outcome of one :meth:`HubNodeExecutor.run` call that actually ran.

    ``transition_id`` is set only when ``wrote_transition`` is true; a
    pending poll, a bounce, or an escalation records no transition row."""

    outcome_choice: str
    to_node_name: str
    wrote_transition: bool
    detail: str = ""
    transition_id: str | None = None


class HubNodeExecutor:
    """Takes one pass of a hub node's action and routes what it answers."""

    def __init__(
        self,
        *,
        facts: IReadChunkFactsRepository,
        artifacts: IWriteChunkArtifactsRepository,
        delivery: IWriteChunkDeliveryRepository,
        hub_exec: IWriteChunkHubExecRepository,
        escalations: IWriteChunkEscalationsRepository,
        events: EventLogService,
        record: IReadChunkRecordRepository,
        graphs: IReadManyGraphs,
        clock: IClock,
        repositories: IReadRepositoryRecordRepository,
        forge_records: IReadForgeRepository,
        links: IReadProjectLinks,
        projects: IReadProjectRepository,
        actions: HubActions,
        passes: ActionPassDriver,
        review_findings: ReviewFindingsRecorder,
        garden_delivery: GardenDeliveryRecorder,
        run_context: IReadRunContextRepository,
        forges: ForgeRegistry,
        requirements: Mapping[str, Requirement] = ACTION_REQUIREMENTS,
        public_url: str | None = None,
        work_sources: IWorkSourceRegistry | None = None,
        slot_stale_after: timedelta = DEFAULT_SLOT_STALE_AFTER,
        tracer: IPlatformTracer | None = None,
    ) -> None:
        self._facts = facts
        self._artifacts = artifacts
        self._delivery = delivery
        self._hub_exec = hub_exec
        self._escalations = escalations
        self._events = events
        self._record = record
        self._graphs = graphs
        self._clock = clock
        self._repositories = repositories
        self._forge_records = forge_records
        self._links = links
        self._projects = projects
        self._actions = actions
        self._passes = passes
        self._review_findings = review_findings
        self._garden_delivery = garden_delivery
        self._run_context = run_context
        self._forges = forges
        self._requirements = requirements
        self._public_url = public_url
        self._work_sources = work_sources
        self._slot_stale_after = slot_stale_after
        self._tracer = tracer or NoopPlatformTracer()

    def record_marker(
        self, chunk_id: str, *, node_id: str, node_name: str, epoch: int, name: str, content: str
    ) -> bool:
        """Record a hub node visit's marker, idempotently per
        ``(chunk, node, name, epoch)``. A superseded epoch writes nothing, preventing
        its marker from landing a repo or enqueuing a close (``bzh:epoch-fencing``). A landing it records names
        no repository record: only a pass's ``LandingRecord`` knows which record landed."""
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
            repo = name.removeprefix(_MARKER_PREFIX)
            self._delivery.record_delivery_repo_landed(
                chunk_id,
                repo=repo,
                commit_hash=content,
                repository_id=None,
                at=self._clock.now(),
            )
        return wrote

    def run(self, chunk: Chunk, graph: Graph, node: Node, *, epoch: int) -> HubRunResult | None:
        """Take one pass of ``node``'s action, or escalate a node naming none; ``None`` if deferred.

        Deferred — the slot is held (by this chunk's own run too), ``node``'s visit at ``epoch`` already
        recorded its exit or was superseded, the chunk is escalated to a person, or this visit is pending
        and not yet due (#66) — is neither an error nor a retry-consuming failure. The due check runs
        BEFORE the slot is acquired, so a pending chunk never contends for it."""
        now = self._clock.now()
        facts = self._facts.load_facts(chunk.chunk_id)
        poll_history = facts.hub_node_poll_history(node_id=node.node_id, epoch=epoch) if facts is not None else []
        if poll_history and now < PollPolicy.of(node).due_at(poll_history[-1]):
            return None  # not yet due — never touches the tenant's slot
        slot_id = self._hub_exec.acquire_hub_exec_slot(
            chunk.chunk_id, node_id=node.node_id, at=now, stale_after=self._slot_stale_after
        )
        if slot_id is None:
            return None
        try:
            # After the acquire: a run that finished between a caller's read and the acquire
            # has recorded its exit by now, so a replay cannot re-run a node it already left.
            facts = self._facts.load_facts(chunk.chunk_id)
            if facts is None or not facts.awaits_exit_from(node, epoch=epoch) or facts.open_escalation() is not None:
                return None
            return self._run_acquired(chunk, graph, node, epoch=epoch, poll_history=poll_history, slot_id=slot_id)
        finally:
            self._hub_exec.release_hub_exec_slot(slot_id, at=self._clock.now())

    def release_orphaned_slots(self) -> int:
        """Release every live hub-execution slot of this executor's tenant — a boot step, not a
        per-request one: a live row when the hub starts belongs to a run the previous process died
        in, so the chunk resumes on its next request rather than after ``slot_stale_after``. Sound
        while one hub process serves the store."""
        return self._hub_exec.release_live_hub_exec_slots(at=self._clock.now())

    def escalate_actionless(self) -> int:
        """Escalate every chunk of this executor's tenant standing at a hub node that names no action — a boot
        step beside :meth:`release_orphaned_slots`, so a chunk the carry-over left stranded reaches a person
        without waiting to be driven. Answers how many it escalated; takes no hub-execution slot."""
        chunks = self._record.get_many(self._record.pinned_to_actionless_hub_nodes())
        graphs = self._graphs.get_many(sorted({chunk.graph_id for chunk in chunks.values()}))
        escalated = 0
        for chunk in chunks.values():
            facts = self._facts.load_facts(chunk.chunk_id)
            graph = graphs.get(chunk.graph_id)
            node = facts.hub_advance_node(graph) if facts is not None and graph is not None else None
            if facts is None or node is None or node.action is not None or facts.open_escalation() is not None:
                continue
            epoch = facts.latest_epoch() or 0
            if not facts.awaits_exit_from(node, epoch=epoch):
                continue
            if self._record_action_missing(chunk, node, epoch=epoch)[1] is BounceEscalationOutcome.RECORDED:
                escalated += 1
        return escalated

    def _run_acquired(
        self,
        chunk: Chunk,
        graph: Graph,
        node: Node,
        *,
        epoch: int,
        poll_history: list[HubNodePollFact],
        slot_id: str,
    ) -> HubRunResult:
        hub_exec = DerivedContext.of(StepKey.attempt(chunk.chunk_id, epoch + 1), SpanRole.HUB_EXEC, slot_id)
        self._tracer.link(hub_exec)
        return self._run_locked(chunk, graph, node, epoch=epoch, poll_history=poll_history, hub_exec=hub_exec)

    def _run_locked(
        self,
        chunk: Chunk,
        graph: Graph,
        node: Node,
        *,
        epoch: int,
        poll_history: list[HubNodePollFact],
        hub_exec: DerivedContext,
    ) -> HubRunResult:
        if node.action is None:
            return self._escalate_action_missing(chunk, node, epoch=epoch)
        if poll_history and self._clock.now() - poll_history[0].polled_at >= PollPolicy.of(node).timeout:
            # The bound is elapsed since the FIRST pending attempt of this visit: stop
            # polling, and never take a pass again this call.
            return self._route_pending_timeout(chunk, graph, node, epoch=epoch)
        return self._take_pass(chunk, graph, node, node.action, epoch=epoch, hub_exec=hub_exec)

    def _escalate_action_missing(self, chunk: Chunk, node: Node, *, epoch: int) -> HubRunResult:
        return self._escalated(*self._record_action_missing(chunk, node, epoch=epoch))

    def _record_action_missing(
        self, chunk: Chunk, node: Node, *, epoch: int
    ) -> tuple[ActionEscalation, BounceEscalationOutcome]:
        """A hub node naming no action — one the carry-over could not translate — escalates its chunk, its
        stored command named, and records ``hub-action-missing`` with the escalation."""
        stored = f"its stored run: {node.legacy_run}" if node.legacy_run else "no `run:`"
        detail = f"hub node `{node.name}` names no action ({stored})"
        escalation = ActionEscalation(EscalationCause.HUB_ACTION_MISSING, detail)
        outcome = self._record_escalation(chunk, escalation, epoch=epoch)
        if outcome is BounceEscalationOutcome.RECORDED:
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
        return escalation, outcome

    def _missing_capability(
        self, action_name: str, resolution: RepositoryResolution
    ) -> tuple[ResolvedRepository, Capability] | None:
        """The first resolved repository whose forge lacks a capability ``action_name`` requires of it, and that
        capability; ``None`` when every forge offers what the action needs. An action naming no requirement
        needs nothing."""
        requirement = self._requirements.get(action_name)
        if requirement is None:
            return None
        for target in resolution.targets:
            offered = self._forges.offered(resolution.forge_of(target), RepositoryId(target.record))
            missing = sorted(requirement(target) - offered)
            if missing:
                return target, missing[0]
        return None

    def _escalate_capability_missing(
        self,
        chunk: Chunk,
        node: Node,
        action_name: str,
        target: ResolvedRepository,
        capability: Capability,
        *,
        forge: ConfiguredForge,
        epoch: int,
    ) -> HubRunResult:
        """Refuse the visit before its pass: only an operator can change what a forge offers or what an action
        asks of it, so the chunk escalates. Its ``hub-log`` artifact is written first, as a pass's log is, once
        per visit; its event only when the escalation takes — a second visit finds the chunk escalated and
        records neither again."""
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
            name=f"{_HUB_LOG_PREFIX}{_CAPABILITY_MISSING_STEP}",
            content=f"[{_CAPABILITY_MISSING_STEP.replace('-', ' ')}]\n{detail}\n",
            at=self._clock.now(),
        )
        escalation = ActionEscalation(EscalationCause.FORGE_CAPABILITY_MISSING, detail)
        outcome = self._record_escalation(chunk, escalation, epoch=epoch)
        if outcome is BounceEscalationOutcome.RECORDED:
            self._record_capability_missing(chunk, node, target, forge, capability, detail, epoch=epoch)
        return self._escalated(escalation, outcome)

    def _record_capability_missing(
        self,
        chunk: Chunk,
        node: Node,
        target: ResolvedRepository,
        forge: ConfiguredForge,
        capability: Capability,
        detail: str,
        *,
        epoch: int,
    ) -> None:
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

    def _visit_forges(
        self, resolution: RepositoryResolution
    ) -> tuple[dict[RepositoryId, RepositoryForge], tuple[str, str] | None]:
        """Each resolved record's forge by record id, or the refusal a secret that cannot be revealed routes
        with."""
        forges: dict[RepositoryId, RepositoryForge] = {}
        for target in resolution.targets:
            repository = RepositoryId(target.record)
            try:
                forges[repository] = self._forges.repository_forge(repository)
            except RepositorySecretUnavailable as exc:
                return {}, ("repository-secret-unavailable", f"repository {target.slug}: {exc}")
        return forges, None

    def _take_pass(
        self, chunk: Chunk, graph: Graph, node: Node, action_name: str, *, epoch: int, hub_exec: DerivedContext
    ) -> HubRunResult:
        """Take one pass of the node's action: resolve the chunk's repositories and refuse as the visit
        cannot run, escalate a forge lacking what the action requires, read the visit, and route what the pass answers.
        A repository's forge is built only for an action that lands."""
        action = self._actions.action(action_name)
        if action is None:
            return self._refuse(
                chunk, graph, node, epoch=epoch, step="action-unavailable", detail=f"no hub action `{action_name}`"
            )
        spec = action.spec
        artifacts = self._artifacts.load_artifacts(chunk.chunk_id)
        project = LandingProject.read(chunk.project_id, projects=self._projects, links=self._links)
        resolved = resolve_visit(
            artifacts,
            project=project,
            minted_at=chunk.minted_at,
            repositories=self._repositories.list_all(include_retired=True),
            forges=self._forge_records.list_all(include_retired=True),
            lands=spec.lands,
        )
        if isinstance(resolved, VisitRefusal):
            if resolved.resolution is not None:
                self._record_refusal_event(chunk, node, resolved.resolution, epoch=epoch)
            return self._refuse(chunk, graph, node, epoch=epoch, step=resolved.step, detail=resolved.detail)
        git_commits, resolution = resolved.git_commits, resolved.resolution
        if (missing := self._missing_capability(action_name, resolution)) is not None:
            target, capability = missing
            return self._escalate_capability_missing(
                chunk, node, action_name, target, capability, forge=resolution.forge_of(target), epoch=epoch
            )
        forges: dict[RepositoryId, RepositoryForge] = {}
        if spec.lands:
            forges, refusal = self._visit_forges(resolution)
            if refusal is not None:
                step, detail = refusal
                return self._refuse(chunk, graph, node, epoch=epoch, step=step, detail=detail)
        visit = Visit(
            chunk=VisitChunk(
                chunk_id=chunk.chunk_id,
                project_id=chunk.project_id,
                minted_at=chunk.minted_at,
                work_refs=tuple(chunk.work_refs),
            ),
            node=VisitNode(
                node_id=node.node_id,
                name=node.name,
                action=action_name,
                action_args=MappingProxyType(dict(node.action_args)),
                expects_git_commits=graph.declares_git_commit,
            ),
            epoch=epoch,
            artifacts=tuple(artifacts),
            markers=tuple(row for row in artifacts if row.node_id == node.node_id and row.kind is ArtifactKind.ASSET),
            git_commits=git_commits,
            resolution=resolution,
            work_items=tuple(MappingProxyType(item) for item in self._resolve_work_items(chunk)),
            feature_title=self._resolve_feature_title(chunk),
            public_url=self._public_url,
            project=project,
        )
        with (
            self._tracer.under(hub_exec),
            self._tracer.child(ACTION_PASS_SPAN, {ACTION_NAME: action_name}),
        ):
            run = self._passes.take(
                action, visit, MappingProxyType(forges), lambda effect: self._write_effect(chunk, node, epoch, effect)
            )
            answer = self._answer(chunk, node, spec, run, epoch=epoch)
            annotate({ACTION_CHOICE: answer.choice or ""})
        log_artifact = self._record_pass_log(chunk, node, action_name, answer, epoch=epoch)
        if answer.escalation is not None:
            return self._escalate(chunk, answer.escalation, epoch=epoch)
        if answer.waits():
            return self._record_pending(chunk, node, epoch=epoch, next_poll_at=answer.next_poll_at)
        commits = [{k: v or "" for k, v in c.items()} for c in git_commits.payload(resolution.qualified, resolution)]
        choice = answer.choice or HUB_DEFAULT_FAILURE_CHOICE
        bounced = BouncedAction(name=action_name, choice=choice, log_artifact=log_artifact)
        return self._route(chunk, graph, node, epoch=epoch, choice=choice, commits=commits, action=bounced)

    def _answer(self, chunk: Chunk, node: Node, spec: ActionSpec, run: PassRun, *, epoch: int) -> Pass:
        """What a pass that ended as ``run`` answers: its own answer; an overrun waits where the action can
        and fails where it cannot; an escaping exception fails, its traceback the pass's log."""
        if run.answer is not None:
            return run.answer
        if run.traceback is not None:
            return Pass(choice=HUB_DEFAULT_FAILURE_CHOICE, log=(run.traceback,))
        self._record_overrun(chunk, node, spec, epoch=epoch)
        if spec.can_wait_for(node.action_args):
            return Pass(choice=_PENDING)
        return Pass(choice=HUB_DEFAULT_FAILURE_CHOICE, log=(f"`{spec.name}` overran its pass bound",))

    def _record_overrun(self, chunk: Chunk, node: Node, spec: ActionSpec, *, epoch: int) -> None:
        self._events.record(
            kind=_EVENT_ACTION_OVERRAN,
            runner_id=None,
            chunk_id=chunk.chunk_id,
            project_id=chunk.project_id,
            lease_id=None,
            node_name=node.name,
            message=f"hub action `{spec.name}` overran its pass bound at node `{node.name}`",
            detail={"action": spec.name, "epoch": epoch},
            at=self._clock.now(),
        )

    def _record_pass_log(self, chunk: Chunk, node: Node, action_name: str, answer: Pass, *, epoch: int) -> str | None:
        """The pass's log lines as its ``hub-log.<action>`` artifact, when it has any; the artifact's name, or
        ``None`` when the pass logged nothing."""
        if not answer.log:
            return None
        name = f"{_HUB_LOG_PREFIX}{action_name}"
        self._artifacts.record_hub_artifact(
            chunk.chunk_id,
            node_id=node.node_id,
            node_name=node.name,
            epoch=epoch,
            admission=EpochAdmission.AT_OR_ABOVE,
            name=name,
            content="\n".join(answer.log) + "\n",
            at=self._clock.now(),
        )
        return name

    def _escalate(self, chunk: Chunk, escalation: ActionEscalation, *, epoch: int) -> HubRunResult:
        """An escalation the hub authors, routing nothing."""
        return self._escalated(escalation, self._record_escalation(chunk, escalation, epoch=epoch))

    def _record_escalation(self, chunk: Chunk, escalation: ActionEscalation, *, epoch: int) -> BounceEscalationOutcome:
        """``escalation`` written as a bounce-cap escalation is — at the next epoch, beside the hub's lease."""
        return self._escalations.record_bounce_escalation(
            chunk.chunk_id,
            epoch=epoch + 1,
            admission=EpochAdmission.ABOVE,
            runner_id=_HUB_RUNNER_ID,
            takeover_command="",
            at=self._clock.now(),
            cause=escalation.cause,
            detail=escalation.detail,
        )

    @staticmethod
    def _escalated(escalation: ActionEscalation, outcome: BounceEscalationOutcome) -> HubRunResult:
        return HubRunResult(
            outcome_choice=escalation.cause.value,
            to_node_name="",
            wrote_transition=False,
            detail=_escalation_detail(escalation.detail, outcome),
        )

    def _write_effect(self, chunk: Chunk, node: Node, epoch: int, effect: Effect) -> EffectResult:
        """Make one effect a pass recorded durable, answering how the hub took it."""
        match effect:
            case Marker(name=name, content=content):
                wrote = self._artifacts.record_hub_artifact(
                    chunk.chunk_id,
                    node_id=node.node_id,
                    node_name=node.name,
                    epoch=epoch,
                    admission=EpochAdmission.AT_OR_ABOVE,
                    name=name,
                    content=content,
                    at=self._clock.now(),
                )
                if wrote or self._artifacts.has_hub_artifact(
                    chunk.chunk_id, node_id=node.node_id, epoch=epoch, name=name
                ):
                    return EffectResult(EffectStatus.RECORDED)
                return EffectResult(EffectStatus.FENCED, _FENCED_DETAIL)
            case LandingRecord(repository=repository, repo=repo, revision=revision):
                refused = self._artifacts.record_landing(
                    chunk.chunk_id,
                    node_id=node.node_id,
                    node_name=node.name,
                    epoch=epoch,
                    admission=EpochAdmission.AT_OR_ABOVE,
                    repo=repo,
                    revision=revision,
                    repository_id=repository,
                    at=self._clock.now(),
                )
                _CP_CLOSE_AFTER_ENQUEUE_BEFORE_DRAIN.reached()
                if refused:
                    return EffectResult(EffectStatus.FENCED, _LANDED_PAST_FENCE_DETAIL)
                return EffectResult(EffectStatus.RECORDED)
            case RecordReviewFindings():
                try:
                    findings = self._review_findings.record(chunk=chunk, node=node, epoch=epoch)
                except ReviewFindingsRejected as exc:
                    return EffectResult(EffectStatus.INVALID, str(exc))
                if findings is ReviewFindingsOutcome.FENCED:
                    return EffectResult(EffectStatus.FENCED, _FENCED_DETAIL)
                return EffectResult(EffectStatus.RECORDED)
            case RecordGardenDelivery(delta=delta, proposals=proposals):
                run = self._run_context.for_chunk(chunk)
                if run is None:
                    return EffectResult(EffectStatus.INVALID, f"no run context for chunk {chunk.chunk_id}")
                try:
                    delivered = self._garden_delivery.record(
                        chunk=chunk, node=node, epoch=epoch, run=run, delta_names=delta, proposal_names=proposals
                    )
                except GardenDeliveryRejected as exc:
                    return EffectResult(EffectStatus.INVALID, str(exc))
                if delivered is DeliveryOutcome.FENCED:
                    return EffectResult(EffectStatus.FENCED, _FENCED_DETAIL)
                return EffectResult(EffectStatus.RECORDED)
            case _:
                raise TypeError(f"no writer for the effect {effect!r}")

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

    def _refuse(self, chunk: Chunk, graph: Graph, node: Node, *, epoch: int, step: str, detail: str) -> HubRunResult:
        """Record why the visit refused to run, and route its failure choice — before any pass is taken."""
        self._artifacts.record_hub_artifact(
            chunk.chunk_id,
            node_id=node.node_id,
            node_name=node.name,
            epoch=epoch,
            admission=EpochAdmission.AT_OR_ABOVE,
            name=f"{_HUB_LOG_PREFIX}{step}",
            content=f"[{step.replace('-', ' ')}]\n{detail}\n",
            at=self._clock.now(),
        )
        return self._route(chunk, graph, node, epoch=epoch, choice=HUB_DEFAULT_FAILURE_CHOICE, commits=[])

    def _record_pending(
        self, chunk: Chunk, node: Node, *, epoch: int, next_poll_at: datetime | None = None
    ) -> HubRunResult:
        """Record one pending-poll-attempt fact (#66) — no transition, and the slot is
        released immediately after this returns. Consumes no retry and no bounce budget:
        pending is the node waiting on external state, not contention or failure. A pass's
        ``next_poll_at`` rides on the row and only ever postpones the next poll."""
        now = self._clock.now()
        self._hub_exec.record_hub_node_poll(
            chunk.chunk_id, node_id=node.node_id, epoch=epoch, at=now, next_poll_at=next_poll_at
        )
        _CP_HUBNODE_AFTER_POLL_BEFORE_SLOT_RELEASE.reached()
        next_poll_at = PollPolicy.of(node).due_at(
            HubNodePollFact(node_id=node.node_id, epoch=epoch, polled_at=now, next_poll_at=next_poll_at)
        )
        return HubRunResult(
            outcome_choice=_PENDING,
            to_node_name=node.name,
            wrote_transition=False,
            detail=f"pending — next poll at {iso_utc(next_poll_at)}",
        )

    def _route_pending_timeout(self, chunk: Chunk, graph: Graph, node: Node, *, epoch: int) -> HubRunResult:
        """A pending node that exceeded its ``poll_timeout`` is a kick-back (#64), not a
        plain failure: record a bounce fact, escalate past the node's ``bounce_cap``,
        else route the ``failure`` edge with the kick-back envelope riding along.
        """
        hub_epoch = epoch + 1
        now = self._clock.now()
        cause = "poll-timeout"
        loaded = self._artifacts.load_artifacts(chunk.chunk_id)
        rows = GitCommits.of(loaded)
        project = LandingProject.read(chunk.project_id, projects=self._projects, links=self._links)
        resolution = resolve_chunk_repositories(
            rows.origins(),
            self._repositories.list_all(include_retired=True),
            self._forge_records.list_all(include_retired=True),
            minted_at=chunk.minted_at,
            linked=project.linked,
            project=project.slug,
        )
        envelope_payload = BounceEnvelope.of(
            cause=cause,
            summary=f"hub node `{node.name}` exceeded its poll_timeout awaiting `{_PENDING}`",
            commits=[{k: v or "" for k, v in c.items()} for c in rows.payload(resolution.qualified)],
            sources=self._delivery_sources(chunk),
            action=None,
        ).serialize()
        self._escalations.record_bounce(chunk.chunk_id, epoch=hub_epoch, cause=cause, envelope=envelope_payload, at=now)

        facts = self._facts.load_facts(chunk.chunk_id)
        cap = node.bounce_cap if node.bounce_cap is not None else DEFAULT_BOUNCE_CAP
        if facts is not None and facts.bounces_over_cap(cap):
            # Hub-authored escalation, no runner runtime dir to compose a wrapped
            # takeover command from — leaves wrapped_takeover_command at its store default.
            crossed = f"bounce cap ({cap}) crossed after {facts.bounce_count()} bounces"
            escalated = self._escalations.record_bounce_escalation(
                chunk.chunk_id,
                epoch=hub_epoch,
                admission=EpochAdmission.ABOVE,
                runner_id=_HUB_RUNNER_ID,
                takeover_command="",
                at=now,
                cause=EscalationCause.BOUNCE_CAP,
                detail=crossed,
            )
            return HubRunResult(
                outcome_choice=HUB_DEFAULT_FAILURE_CHOICE,
                to_node_name="",
                wrote_transition=False,
                detail=f"poll_timeout exceeded — {_escalation_detail(crossed, escalated)}",
            )
        artifact = StoredArtifact(
            kind=ArtifactKind.ASSET,
            name="bounce-envelope",
            data=envelope_payload,
            repo=None,
            forge=None,
            artifact_id=Id.mint(IdPrefix.ARTIFACT, self._clock).value,
            chunk_id=chunk.chunk_id,
            node_id=node.node_id,
            node_name=node.name,
            epoch=hub_epoch,
        )
        return self._route(
            chunk, graph, node, epoch=epoch, choice=HUB_DEFAULT_FAILURE_CHOICE, extra_artifacts=[artifact]
        )

    def _route(
        self,
        chunk: Chunk,
        graph: Graph,
        node: Node,
        *,
        epoch: int,
        choice: str,
        extra_artifacts: list[StoredArtifact] | None = None,
        commits: list[dict[str, str]] | None = None,
        action: BouncedAction | None = None,
    ) -> HubRunResult:
        edge = graph.edge_for_choice(node.node_id, choice)
        if edge is None:
            # Reachable only for a stored graph that lacks an edge mint now requires: nothing
            # routes, so the chunk escalates to a person rather than taking the pass again.
            now = self._clock.now()
            detail = f"no authored edge for choice `{choice}` on hub node `{node.name}`"
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
            unroutable = ActionEscalation(
                EscalationCause.UNROUTABLE_OUTCOME, f"hub node `{node.name}` answered `{choice}`, which no edge routes"
            )
            return self._escalate(chunk, unroutable, epoch=epoch)
        if edge.to_node_name == RESERVED_TERMINAL:
            to_node_id: str | None = RESERVED_TERMINAL
        else:
            target = graph.node_by_name(edge.to_node_name)
            to_node_id = target.node_id if target is not None else None
        if to_node_id is None:
            return HubRunResult(
                outcome_choice=choice,
                to_node_name=edge.to_node_name,
                wrote_transition=False,
                detail=f"choice `{choice}` routes to unknown node {edge.to_node_name}",
            )
        hub_epoch = epoch + 1

        # A delivery kick-back (#64), detected from the landed-marker fact and never from
        # the choice name — no outcome name is privileged (#67).
        if commits and to_node_id != RESERVED_TERMINAL:
            pending_repos = {c["repo"] for c in commits}
            loaded = self._artifacts.load_artifacts(chunk.chunk_id)
            if not pending_repos.issubset(LandedRepos.of(loaded).names):
                now = self._clock.now()
                envelope_payload = BounceEnvelope.of(
                    cause=choice,
                    summary=f"hub node `{node.name}` routed `{choice}` to `{edge.to_node_name}` — delivery incomplete",
                    commits=commits,
                    sources=self._delivery_sources(chunk),
                    action=action,
                ).serialize()
                self._escalations.record_bounce(
                    chunk.chunk_id, epoch=hub_epoch, cause=choice, envelope=envelope_payload, at=now
                )
                facts = self._facts.load_facts(chunk.chunk_id)
                cap = node.bounce_cap if node.bounce_cap is not None else DEFAULT_BOUNCE_CAP
                if facts is not None and facts.bounces_over_cap(cap):
                    # Hub-authored escalation, no runner runtime dir to compose a wrapped
                    # takeover command from — leaves wrapped_takeover_command at its store default.
                    crossed = f"bounce cap ({cap}) crossed after {facts.bounce_count()} bounces"
                    escalated = self._escalations.record_bounce_escalation(
                        chunk.chunk_id,
                        epoch=hub_epoch,
                        admission=EpochAdmission.ABOVE,
                        runner_id=_HUB_RUNNER_ID,
                        takeover_command="",
                        at=now,
                        cause=EscalationCause.BOUNCE_CAP,
                        detail=crossed,
                    )
                    return HubRunResult(
                        outcome_choice=choice,
                        to_node_name="",
                        wrote_transition=False,
                        detail=_escalation_detail(crossed, escalated),
                    )
                envelope_artifact = StoredArtifact(
                    kind=ArtifactKind.ASSET,
                    name="bounce-envelope",
                    data=envelope_payload,
                    repo=None,
                    forge=None,
                    artifact_id=Id.mint(IdPrefix.ARTIFACT, self._clock).value,
                    chunk_id=chunk.chunk_id,
                    node_id=node.node_id,
                    node_name=node.name,
                    epoch=hub_epoch,
                )
                extra_artifacts = [*(extra_artifacts or []), envelope_artifact]

        fresh_transition_id = Id.mint(IdPrefix.TRANSITION, self._clock).value
        wrote = self._hub_exec.record_hub_step_transition(
            chunk.chunk_id,
            from_node_id=node.node_id,
            to_node_id=to_node_id,
            choice_name=choice,
            epoch=hub_epoch,
            admission=EpochAdmission.ABOVE,
            runner_id=_HUB_RUNNER_ID,
            transition_id=fresh_transition_id,
            at=self._clock.now(),
            artifacts=extra_artifacts or [],
            release_route=to_node_id == RESERVED_TERMINAL,
        )
        return HubRunResult(
            outcome_choice=choice,
            to_node_name=edge.to_node_name,
            wrote_transition=wrote,
            transition_id=fresh_transition_id if wrote else None,
        )

    def _resolve_feature_title(self, chunk: Chunk) -> str | None:
        """The chunk's prose feature title (:data:`ENV_FEATURE_TITLE`) — the FIRST
        ``work_ref``'s work item title, best-effort. A read failure or a missing
        registry, pointer, or title degrades to ``None`` rather than breaking delivery."""
        pointer = chunk.originating_ref()
        if pointer is None or self._work_sources is None:
            return None
        source = self._work_sources.get(pointer.source)
        if source is None:
            return None
        try:
            title = source.fetch(pointer).title
        except Exception:  # a forge read failing must never break delivery — degrade to no title
            return None
        return title or None

    def _resolve_work_items(self, chunk: Chunk) -> tuple[dict[str, str | None], ...]:
        """One ``{label, reference}`` per ``work_ref``, with no forge read; ``reference`` is ``None`` off-forge."""
        items: list[dict[str, str | None]] = []
        for pointer in chunk.work_refs:
            source = self._work_sources.get(pointer.source) if self._work_sources is not None else None
            label = self._work_sources.label(pointer) if self._work_sources is not None else None
            items.append(
                {
                    "label": label or f"{pointer.source}:{pointer.ref}",
                    "reference": source.forge_reference(pointer) if source is not None else None,
                }
            )
        return tuple(items)
