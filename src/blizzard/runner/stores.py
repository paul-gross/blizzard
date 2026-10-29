"""The runner-store bundles and umbrella Protocols.

``RunnerStores`` is the frozen bundle of write-capable concept Protocol seams over the runner-scoped tables, every
adapter bound to one runner. ``RunnerReadStores`` narrows it statically, one field per concept typed to its ``IRead*``
twin, over the same adapter instances. ``IReadRunnerStore``/``IWriteRunnerStore`` compose every runner-scoped concept
Protocol into one seam.

``HostStores`` is its host-scoped sibling, built once per process beside it: the adapters over the tables that belong
to this host rather than to the runner it is bound to, and the one member exempt from the runner statement guard — the
runner audit, which reads across runners. ``HostReadStores`` is its read-only narrowing.
Host tables are reached only through it.

This module, not ``runner/store/``, is their home, since no Protocol may be declared there."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from blizzard.runner.auth.jti_cache import IJtiCache
from blizzard.runner.auth.tokens import IReadTokenRepository, IWriteTokenRepository
from blizzard.runner.environments.repository import (
    EnvBinding,
    IReadEnvironmentRepository,
    IWriteEnvironmentRepository,
)
from blizzard.runner.harness.selftest_result import (
    IReadSelfTestResultRepository,
    IWriteSelfTestResultRepository,
)
from blizzard.runner.harness.workspace_prompts import IReadWorkspacePromptRepository, IWriteWorkspacePromptRepository
from blizzard.runner.hub.host_identity import IReadHostIdentityRepository, IWriteHostIdentityRepository
from blizzard.runner.hub.identity import IReadRunnerIdentityRepository, IWriteRunnerIdentityRepository
from blizzard.runner.hub.outbound_buffer import IReadOutboundRepository, IWriteOutboundRepository
from blizzard.runner.hub.served_projects import IReadServedProjectsRepository, IWriteServedProjectsRepository
from blizzard.runner.leases.asks import IReadAskRepository, IWriteAskRepository
from blizzard.runner.leases.elicitation import IReadElicitationRepository, IWriteElicitationRepository
from blizzard.runner.leases.escalations import IReadEscalationRepository, IWriteEscalationRepository
from blizzard.runner.leases.git_commit_declaration import IReadGitCommitDeclarationRepository
from blizzard.runner.leases.liveness import IReadLeaseLivenessRepository, IWriteLeaseLivenessRepository
from blizzard.runner.leases.operator_requests import (
    IReadAttachmentRepository,
    IReadRequeueRepository,
    IWriteAttachmentRepository,
    IWriteRequeueRepository,
)
from blizzard.runner.leases.overload import IReadOverloadRepository, IWriteOverloadRepository
from blizzard.runner.leases.record import IReadLeaseRecordRepository, IWriteLeaseRecordRepository
from blizzard.runner.leases.resume_intent import IReadLeaseResumeIntentRepository, IWriteLeaseResumeIntentRepository
from blizzard.runner.leases.session import IReadLeaseSessionRepository, IWriteLeaseSessionRepository
from blizzard.runner.lifecycle.artifacts import IReadGraphArtifactRepository, IWriteGraphArtifactRepository
from blizzard.runner.lifecycle.daemon_liveness import IReadDaemonLivenessRepository, IWriteDaemonLivenessRepository
from blizzard.runner.lifecycle.judgement.checks import IReadCheckRepository, IWriteCheckRepository
from blizzard.runner.lifecycle.judgement.git_commit_declaration import IWriteGitCommitDeclarationRepository
from blizzard.runner.lifecycle.takeover import IReadTakeoverRepository, IWriteTakeoverRepository
from blizzard.runner.throttle.pause import IReadPauseRepository, IWritePauseRepository
from blizzard.runner.throttle.usage_limits import (
    IReadRunnerUsageLimitRepository,
    IReadSubscriptionUsageLimitRepository,
    IWriteRunnerUsageLimitRepository,
    IWriteSubscriptionUsageLimitRepository,
)
from blizzard.runner.tracing.repository import IReadLeaseTraces, IWriteLeaseTraces
from blizzard.runner.transcripts.invocation_boundaries import (
    IReadInvocationBoundaryRepository,
    IWriteInvocationBoundaryRepository,
)
from blizzard.runner.transcripts.ledger import IReadTranscriptLedgerRepository, IWriteTranscriptLedgerRepository
from blizzard.runner.transfer.rows import IReadHostIngestRepository, IWriteHostIngestRepository
from blizzard.runner.usage.repository import (
    IReadExternalUsageRepository,
    IReadHostLaneRepository,
    IReadUsageRepository,
    IWriteExternalUsageRepository,
    IWriteHostLaneRepository,
    IWriteUsageRepository,
)

__all__ = [
    "HostReadStores",
    "HostStores",
    "IReadRunnerStore",
    "IRunnerAudit",
    "IRunnerBinding",
    "IWriteRunnerStore",
    "RunnerReadStores",
    "RunnerStores",
]


class IRunnerBinding(Protocol):
    """The runner a store is bound to: the id the host file configures for it, or ``None`` while it is bound to none."""

    @property
    def runner_id(self) -> str | None: ...


class IReadRunnerStore(
    IReadLeaseRecordRepository,
    IReadLeaseSessionRepository,
    IReadLeaseLivenessRepository,
    IReadLeaseResumeIntentRepository,
    IReadEnvironmentRepository,
    IReadTranscriptLedgerRepository,
    IReadTokenRepository,
    IReadOutboundRepository,
    IReadOverloadRepository,
    IReadAskRepository,
    IReadPauseRepository,
    IReadRunnerIdentityRepository,
    IReadServedProjectsRepository,
    IReadTakeoverRepository,
    IReadRequeueRepository,
    IReadEscalationRepository,
    IReadUsageRepository,
    IReadAttachmentRepository,
    IReadGitCommitDeclarationRepository,
    IReadCheckRepository,
    IReadElicitationRepository,
    IReadInvocationBoundaryRepository,
    IReadSelfTestResultRepository,
    IReadLeaseTraces,
    IReadRunnerUsageLimitRepository,
    Protocol,
):
    """Read-only runner-store queries, every concept's read seam composed."""


class IWriteRunnerStore(
    IWriteLeaseRecordRepository,
    IWriteLeaseSessionRepository,
    IWriteLeaseLivenessRepository,
    IWriteLeaseResumeIntentRepository,
    IWriteEnvironmentRepository,
    IWriteTranscriptLedgerRepository,
    IWriteTokenRepository,
    IWriteOutboundRepository,
    IWriteOverloadRepository,
    IWriteAskRepository,
    IWritePauseRepository,
    IWriteRunnerIdentityRepository,
    IWriteServedProjectsRepository,
    IWriteTakeoverRepository,
    IWriteRequeueRepository,
    IWriteEscalationRepository,
    IWriteUsageRepository,
    IWriteAttachmentRepository,
    IWriteGitCommitDeclarationRepository,
    IWriteCheckRepository,
    IWriteElicitationRepository,
    IWriteInvocationBoundaryRepository,
    IWriteSelfTestResultRepository,
    IWriteLeaseTraces,
    IWriteRunnerUsageLimitRepository,
    IReadRunnerStore,
    Protocol,
):
    """Read-write runner store, every concept's write seam composed."""


@dataclass(frozen=True)
class RunnerStores:
    """The wired concept-store collaborators, each typed to its write Protocol."""

    lease_record: IWriteLeaseRecordRepository
    session: IWriteLeaseSessionRepository
    liveness: IWriteLeaseLivenessRepository
    resume_intent: IWriteLeaseResumeIntentRepository
    environments: IWriteEnvironmentRepository
    transcript_ledger: IWriteTranscriptLedgerRepository
    tokens: IWriteTokenRepository
    outbound: IWriteOutboundRepository
    overload: IWriteOverloadRepository
    asks: IWriteAskRepository
    pause: IWritePauseRepository
    identity: IWriteRunnerIdentityRepository
    served_projects: IWriteServedProjectsRepository
    takeover: IWriteTakeoverRepository
    requeue: IWriteRequeueRepository
    escalations: IWriteEscalationRepository
    usage: IWriteUsageRepository
    attachments: IWriteAttachmentRepository
    git_commit_declarations: IWriteGitCommitDeclarationRepository
    checks: IWriteCheckRepository
    elicitations: IWriteElicitationRepository
    invocation_boundaries: IWriteInvocationBoundaryRepository
    selftest_results: IWriteSelfTestResultRepository
    lease_traces: IWriteLeaseTraces
    #: The runner's own usage-limit marks and floor clears — its floor when it spends no subscription.
    usage_limits: IWriteRunnerUsageLimitRepository


@dataclass(frozen=True)
class RunnerReadStores:
    """The read-only runner-store bundle — every field typed to its concept's read
    Protocol only, over the same instances :meth:`of` is given."""

    lease_record: IReadLeaseRecordRepository
    session: IReadLeaseSessionRepository
    liveness: IReadLeaseLivenessRepository
    resume_intent: IReadLeaseResumeIntentRepository
    environments: IReadEnvironmentRepository
    transcript_ledger: IReadTranscriptLedgerRepository
    tokens: IReadTokenRepository
    outbound: IReadOutboundRepository
    overload: IReadOverloadRepository
    asks: IReadAskRepository
    pause: IReadPauseRepository
    identity: IReadRunnerIdentityRepository
    served_projects: IReadServedProjectsRepository
    takeover: IReadTakeoverRepository
    requeue: IReadRequeueRepository
    escalations: IReadEscalationRepository
    usage: IReadUsageRepository
    attachments: IReadAttachmentRepository
    git_commit_declarations: IReadGitCommitDeclarationRepository
    checks: IReadCheckRepository
    elicitations: IReadElicitationRepository
    invocation_boundaries: IReadInvocationBoundaryRepository
    selftest_results: IReadSelfTestResultRepository
    lease_traces: IReadLeaseTraces
    usage_limits: IReadRunnerUsageLimitRepository

    @classmethod
    def of(cls, stores: RunnerStores) -> RunnerReadStores:
        """Narrow ``stores`` to its read-only twin — every ``IWrite*`` field re-typed to its
        ``IRead*`` twin over the same instance, never a second one."""
        return cls(
            lease_record=stores.lease_record,
            session=stores.session,
            liveness=stores.liveness,
            resume_intent=stores.resume_intent,
            environments=stores.environments,
            transcript_ledger=stores.transcript_ledger,
            tokens=stores.tokens,
            outbound=stores.outbound,
            overload=stores.overload,
            asks=stores.asks,
            pause=stores.pause,
            identity=stores.identity,
            served_projects=stores.served_projects,
            takeover=stores.takeover,
            requeue=stores.requeue,
            escalations=stores.escalations,
            usage=stores.usage,
            attachments=stores.attachments,
            git_commit_declarations=stores.git_commit_declarations,
            checks=stores.checks,
            elicitations=stores.elicitations,
            invocation_boundaries=stores.invocation_boundaries,
            selftest_results=stores.selftest_results,
            lease_traces=stores.lease_traces,
            usage_limits=stores.usage_limits,
        )


class IRunnerAudit(Protocol):
    """The reads that span every runner: the invariant checker's audit, which runners the store's identity rows name,
    and what a workspace's shared pool holds."""

    def connect(self) -> AbstractContextManager[Any]:
        """A store connection whose statements are exempt from the runner statement guard, closed on leaving the
        ``with``. Typed ``Any``: the audit runs driver statements of its own, and no driver type is named here."""
        ...

    def named_runners(self) -> tuple[str, ...]:
        """The ids of the runners the store's identity rows name, oldest first — empty for a store none has joined."""
        ...

    def held_workspace_bindings(self, workspace_id: str) -> list[EnvBinding]:
        """Every runner's held bindings in the workspace's pool, plus every held binding naming no workspace (an
        unknown row only shrinks a pool), oldest first."""
        ...


@dataclass(frozen=True)
class HostStores:
    """The wired host-scoped collaborators, each typed to its write Protocol, plus the named member exempt from the
    runner statement guard."""

    workspace_prompt: IWriteWorkspacePromptRepository
    graph_artifacts: IWriteGraphArtifactRepository
    jti_cache: IJtiCache
    daemon_liveness: IWriteDaemonLivenessRepository
    external_usage: IWriteExternalUsageRepository
    host_lane: IWriteHostLaneRepository
    host_identity: IWriteHostIdentityRepository
    #: The host half of a runner transfer into this store, and the ingests it recorded.
    host_ingests: IWriteHostIngestRepository
    #: Each subscription's usage-limit marks and floor clears, shared by every runner spending it.
    usage_limits: IWriteSubscriptionUsageLimitRepository
    #: Exempt: reads every runner's rows at once — the invariant audit, ``init``'s which-runners read, and a workspace
    #: pool's held read.
    runner_audit: IRunnerAudit


@dataclass(frozen=True)
class HostReadStores:
    """The read-only host-store bundle — the host-scoped concepts typed to their read Protocols, over the same
    instances :meth:`of` is given; the exempt member is not carried."""

    workspace_prompt: IReadWorkspacePromptRepository
    graph_artifacts: IReadGraphArtifactRepository
    daemon_liveness: IReadDaemonLivenessRepository
    external_usage: IReadExternalUsageRepository
    host_identity: IReadHostIdentityRepository
    host_lane: IReadHostLaneRepository
    host_ingests: IReadHostIngestRepository
    usage_limits: IReadSubscriptionUsageLimitRepository

    @classmethod
    def of(cls, stores: HostStores) -> HostReadStores:
        return cls(
            workspace_prompt=stores.workspace_prompt,
            graph_artifacts=stores.graph_artifacts,
            daemon_liveness=stores.daemon_liveness,
            external_usage=stores.external_usage,
            host_identity=stores.host_identity,
            host_lane=stores.host_lane,
            host_ingests=stores.host_ingests,
            usage_limits=stores.usage_limits,
        )
