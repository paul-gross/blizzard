"""The runner-store bundles and umbrella Protocols (blizzard#410, blizzard#412, D4).

``RunnerStores`` is the frozen bundle of write-capable Protocol seams
:mod:`~blizzard.runner.composition` builds, for a collaborator spanning several concepts.
``RunnerReadStores`` narrows it statically, one field per concept typed to its ``IRead*``
twin, over the same adapter instances (D1) — the bundle every route resolves, so
``bzh:controller-read-only`` holds at type-check time for the one collaborator every
handler reaches through. ``IReadRunnerStore``/``IWriteRunnerStore`` compose every concept
Protocol into one seam — no ``src/`` collaborator holds either directly, every one now
narrowed to a concept Protocol or one of the two bundles above, but ``tests/runner_fakes.py``
still takes ``IWriteRunnerStore`` to type a fake that structurally satisfies every concept at
once, and the write-protocol census gate walks both to check every concept's write-only
surface. This module, not ``runner/store/``, is their home, since no Protocol may be declared
there."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.runner.auth.tokens import IReadTokenRepository, IWriteTokenRepository
from blizzard.runner.domain.artifacts import IReadGraphArtifactRepository, IWriteGraphArtifactRepository
from blizzard.runner.domain.asks import IReadAskRepository, IWriteAskRepository
from blizzard.runner.domain.attachments import IReadAttachmentRepository, IWriteAttachmentRepository
from blizzard.runner.domain.checks import IReadCheckRepository, IWriteCheckRepository
from blizzard.runner.domain.elicitation import IReadElicitationRepository, IWriteElicitationRepository
from blizzard.runner.domain.escalations import IReadEscalationRepository, IWriteEscalationRepository
from blizzard.runner.domain.git_commit_declaration import (
    IReadGitCommitDeclarationRepository,
    IWriteGitCommitDeclarationRepository,
)
from blizzard.runner.domain.leases import (
    IReadLeaseLivenessRepository,
    IReadLeaseRecordRepository,
    IReadLeaseResumeIntentRepository,
    IReadLeaseSessionRepository,
    IWriteLeaseLivenessRepository,
    IWriteLeaseRecordRepository,
    IWriteLeaseResumeIntentRepository,
    IWriteLeaseSessionRepository,
)
from blizzard.runner.domain.outbound import IReadOutboundRepository, IWriteOutboundRepository
from blizzard.runner.domain.pause import IReadPauseRepository, IWritePauseRepository
from blizzard.runner.domain.requeue import IReadRequeueRepository, IWriteRequeueRepository
from blizzard.runner.domain.takeover import IReadTakeoverRepository, IWriteTakeoverRepository
from blizzard.runner.domain.usage import IReadUsageRepository, IWriteUsageRepository
from blizzard.runner.environments.repository import IReadEnvironmentRepository, IWriteEnvironmentRepository
from blizzard.runner.harness.workspace_prompts import IReadWorkspacePromptRepository, IWriteWorkspacePromptRepository
from blizzard.runner.transcripts.ledger import IReadTranscriptLedgerRepository, IWriteTranscriptLedgerRepository

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
    IReadAskRepository,
    IReadPauseRepository,
    IReadTakeoverRepository,
    IReadRequeueRepository,
    IReadEscalationRepository,
    IReadUsageRepository,
    IReadAttachmentRepository,
    IReadGitCommitDeclarationRepository,
    IReadCheckRepository,
    IReadGraphArtifactRepository,
    IReadElicitationRepository,
    Protocol,
):
    """Read-only runner-store queries, every concept's read seam composed (held by
    read-path edges) — the pre-narrowing umbrella a many-concept collaborator still takes
    directly."""


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
    IWriteAskRepository,
    IWritePauseRepository,
    IWriteTakeoverRepository,
    IWriteRequeueRepository,
    IWriteEscalationRepository,
    IWriteUsageRepository,
    IWriteAttachmentRepository,
    IWriteGitCommitDeclarationRepository,
    IWriteCheckRepository,
    IWriteGraphArtifactRepository,
    IWriteElicitationRepository,
    IReadRunnerStore,
    Protocol,
):
    """Read-write runner store, every concept's write seam composed — held only by the
    domain (the loop steps)."""


@dataclass(frozen=True)
class RunnerStores:
    """The wired concept-store collaborators, built by
    :func:`~blizzard.runner.composition.build_stores`."""

    lease_record: IWriteLeaseRecordRepository
    session: IWriteLeaseSessionRepository
    liveness: IWriteLeaseLivenessRepository
    resume_intent: IWriteLeaseResumeIntentRepository
    environments: IWriteEnvironmentRepository
    transcript_ledger: IWriteTranscriptLedgerRepository
    tokens: IWriteTokenRepository
    workspace_prompt: IWriteWorkspacePromptRepository
    outbound: IWriteOutboundRepository
    asks: IWriteAskRepository
    pause: IWritePauseRepository
    takeover: IWriteTakeoverRepository
    requeue: IWriteRequeueRepository
    escalations: IWriteEscalationRepository
    usage: IWriteUsageRepository
    attachments: IWriteAttachmentRepository
    git_commit_declarations: IWriteGitCommitDeclarationRepository
    checks: IWriteCheckRepository
    graph_artifacts: IWriteGraphArtifactRepository
    elicitations: IWriteElicitationRepository


@dataclass(frozen=True)
class RunnerReadStores:
    """The controller-facing runner-store bundle — every field typed to its concept's
    read Protocol only, so ``bzh:controller-read-only`` is enforced at type-check time for
    the one collaborator every route handler reaches through. Narrows statically over the
    same adapter instances :func:`of` is given (D1) — it wraps nothing and opens no second
    connection."""

    lease_record: IReadLeaseRecordRepository
    session: IReadLeaseSessionRepository
    liveness: IReadLeaseLivenessRepository
    resume_intent: IReadLeaseResumeIntentRepository
    environments: IReadEnvironmentRepository
    transcript_ledger: IReadTranscriptLedgerRepository
    tokens: IReadTokenRepository
    workspace_prompt: IReadWorkspacePromptRepository
    outbound: IReadOutboundRepository
    asks: IReadAskRepository
    pause: IReadPauseRepository
    takeover: IReadTakeoverRepository
    requeue: IReadRequeueRepository
    escalations: IReadEscalationRepository
    usage: IReadUsageRepository
    attachments: IReadAttachmentRepository
    git_commit_declarations: IReadGitCommitDeclarationRepository
    checks: IReadCheckRepository
    graph_artifacts: IReadGraphArtifactRepository
    elicitations: IReadElicitationRepository

    @classmethod
    def of(cls, stores: RunnerStores) -> RunnerReadStores:
        """Narrow ``stores`` to its read-only twin — every ``IWrite*`` field re-typed to its
        ``IRead*`` twin over the same instance, never a second one (D1)."""
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
            asks=stores.asks,
            pause=stores.pause,
            takeover=stores.takeover,
            requeue=stores.requeue,
            escalations=stores.escalations,
            usage=stores.usage,
            attachments=stores.attachments,
            git_commit_declarations=stores.git_commit_declarations,
            checks=stores.checks,
            graph_artifacts=stores.graph_artifacts,
            elicitations=stores.elicitations,
        )
