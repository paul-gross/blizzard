"""The runner-store bundles and umbrella Protocols.

``RunnerStores`` is the frozen bundle of write-capable concept Protocol seams.
``RunnerReadStores`` narrows it statically, one field per concept typed to its ``IRead*``
twin, over the same adapter instances. ``IReadRunnerStore``/``IWriteRunnerStore`` compose
every concept Protocol into one seam. This module, not ``runner/store/``, is their home,
since no Protocol may be declared there."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.runner.auth.tokens import IReadTokenRepository, IWriteTokenRepository
from blizzard.runner.environments.repository import IReadEnvironmentRepository, IWriteEnvironmentRepository
from blizzard.runner.harness.selftest_result import (
    IReadSelfTestResultRepository,
    IWriteSelfTestResultRepository,
)
from blizzard.runner.harness.workspace_prompts import IReadWorkspacePromptRepository, IWriteWorkspacePromptRepository
from blizzard.runner.hub.identity import IReadRunnerIdentityRepository, IWriteRunnerIdentityRepository
from blizzard.runner.hub.outbound_buffer import IReadOutboundRepository, IWriteOutboundRepository
from blizzard.runner.leases import (
    IReadLeaseLivenessRepository,
    IReadLeaseRecordRepository,
    IReadLeaseResumeIntentRepository,
    IReadLeaseSessionRepository,
    IWriteLeaseLivenessRepository,
    IWriteLeaseRecordRepository,
    IWriteLeaseResumeIntentRepository,
    IWriteLeaseSessionRepository,
)
from blizzard.runner.leases.asks import IReadAskRepository, IWriteAskRepository
from blizzard.runner.leases.elicitation import IReadElicitationRepository, IWriteElicitationRepository
from blizzard.runner.leases.escalations import IReadEscalationRepository, IWriteEscalationRepository
from blizzard.runner.leases.operator_requests import (
    IReadAttachmentRepository,
    IReadRequeueRepository,
    IWriteAttachmentRepository,
    IWriteRequeueRepository,
)
from blizzard.runner.leases.overload import IReadOverloadRepository, IWriteOverloadRepository
from blizzard.runner.lifecycle.judgement.artifacts import IReadGraphArtifactRepository, IWriteGraphArtifactRepository
from blizzard.runner.lifecycle.judgement.checks import IReadCheckRepository, IWriteCheckRepository
from blizzard.runner.lifecycle.judgement.git_commit_declaration import (
    IReadGitCommitDeclarationRepository,
    IWriteGitCommitDeclarationRepository,
)
from blizzard.runner.lifecycle.takeover import IReadTakeoverRepository, IWriteTakeoverRepository
from blizzard.runner.throttle.pause import IReadPauseRepository, IWritePauseRepository
from blizzard.runner.tracing.repository import IReadLeaseTraces, IWriteLeaseTraces
from blizzard.runner.transcripts.invocation_boundaries import (
    IReadInvocationBoundaryRepository,
    IWriteInvocationBoundaryRepository,
)
from blizzard.runner.transcripts.ledger import IReadTranscriptLedgerRepository, IWriteTranscriptLedgerRepository
from blizzard.runner.usage.repository import IReadUsageRepository, IWriteUsageRepository

__all__ = ["IReadRunnerStore", "IWriteRunnerStore", "RunnerReadStores", "RunnerStores"]


class IReadRunnerStore(
    IReadLeaseRecordRepository,
    IReadLeaseSessionRepository,
    IReadLeaseLivenessRepository,
    IReadLeaseResumeIntentRepository,
    IReadEnvironmentRepository,
    IReadTranscriptLedgerRepository,
    IReadTokenRepository,
    IReadWorkspacePromptRepository,
    IReadOutboundRepository,
    IReadOverloadRepository,
    IReadAskRepository,
    IReadPauseRepository,
    IReadRunnerIdentityRepository,
    IReadTakeoverRepository,
    IReadRequeueRepository,
    IReadEscalationRepository,
    IReadUsageRepository,
    IReadAttachmentRepository,
    IReadGitCommitDeclarationRepository,
    IReadCheckRepository,
    IReadGraphArtifactRepository,
    IReadElicitationRepository,
    IReadInvocationBoundaryRepository,
    IReadSelfTestResultRepository,
    IReadLeaseTraces,
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
    IWriteWorkspacePromptRepository,
    IWriteOutboundRepository,
    IWriteOverloadRepository,
    IWriteAskRepository,
    IWritePauseRepository,
    IWriteRunnerIdentityRepository,
    IWriteTakeoverRepository,
    IWriteRequeueRepository,
    IWriteEscalationRepository,
    IWriteUsageRepository,
    IWriteAttachmentRepository,
    IWriteGitCommitDeclarationRepository,
    IWriteCheckRepository,
    IWriteGraphArtifactRepository,
    IWriteElicitationRepository,
    IWriteInvocationBoundaryRepository,
    IWriteSelfTestResultRepository,
    IWriteLeaseTraces,
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
    workspace_prompt: IWriteWorkspacePromptRepository
    outbound: IWriteOutboundRepository
    overload: IWriteOverloadRepository
    asks: IWriteAskRepository
    pause: IWritePauseRepository
    identity: IWriteRunnerIdentityRepository
    takeover: IWriteTakeoverRepository
    requeue: IWriteRequeueRepository
    escalations: IWriteEscalationRepository
    usage: IWriteUsageRepository
    attachments: IWriteAttachmentRepository
    git_commit_declarations: IWriteGitCommitDeclarationRepository
    checks: IWriteCheckRepository
    graph_artifacts: IWriteGraphArtifactRepository
    elicitations: IWriteElicitationRepository
    invocation_boundaries: IWriteInvocationBoundaryRepository
    selftest_results: IWriteSelfTestResultRepository
    lease_traces: IWriteLeaseTraces


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
    workspace_prompt: IReadWorkspacePromptRepository
    outbound: IReadOutboundRepository
    overload: IReadOverloadRepository
    asks: IReadAskRepository
    pause: IReadPauseRepository
    identity: IReadRunnerIdentityRepository
    takeover: IReadTakeoverRepository
    requeue: IReadRequeueRepository
    escalations: IReadEscalationRepository
    usage: IReadUsageRepository
    attachments: IReadAttachmentRepository
    git_commit_declarations: IReadGitCommitDeclarationRepository
    checks: IReadCheckRepository
    graph_artifacts: IReadGraphArtifactRepository
    elicitations: IReadElicitationRepository
    invocation_boundaries: IReadInvocationBoundaryRepository
    selftest_results: IReadSelfTestResultRepository
    lease_traces: IReadLeaseTraces

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
            workspace_prompt=stores.workspace_prompt,
            outbound=stores.outbound,
            overload=stores.overload,
            asks=stores.asks,
            pause=stores.pause,
            identity=stores.identity,
            takeover=stores.takeover,
            requeue=stores.requeue,
            escalations=stores.escalations,
            usage=stores.usage,
            attachments=stores.attachments,
            git_commit_declarations=stores.git_commit_declarations,
            checks=stores.checks,
            graph_artifacts=stores.graph_artifacts,
            elicitations=stores.elicitations,
            invocation_boundaries=stores.invocation_boundaries,
            selftest_results=stores.selftest_results,
            lease_traces=stores.lease_traces,
        )
