"""The runner-store bundle and umbrella Protocols (blizzard#410, D4).

``RunnerStores`` is the frozen bundle of write-capable Protocol seams
:mod:`~blizzard.runner.composition` builds, for a collaborator spanning several concepts.
``IReadRunnerStore``/``IWriteRunnerStore`` compose every concept Protocol into one seam —
no ``src/`` collaborator holds either directly, every one now narrowed to a concept Protocol
or the ``RunnerStores`` bundle, but ``tests/runner_fakes.py`` still takes ``IWriteRunnerStore``
to type a fake that structurally satisfies every concept at once, and the write-protocol
census gate walks both to check every concept's write-only surface. This module, not
``runner/store/``, is their home, since no Protocol may be declared there."""

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
from blizzard.runner.domain.leases import IReadLeaseRepository, IWriteLeaseRepository
from blizzard.runner.domain.outbound import IReadOutboundRepository, IWriteOutboundRepository
from blizzard.runner.domain.pause import IReadPauseRepository, IWritePauseRepository
from blizzard.runner.domain.requeue import IReadRequeueRepository, IWriteRequeueRepository
from blizzard.runner.domain.takeover import IReadTakeoverRepository, IWriteTakeoverRepository
from blizzard.runner.domain.usage import IReadUsageRepository, IWriteUsageRepository
from blizzard.runner.environments.repository import IReadEnvironmentRepository, IWriteEnvironmentRepository
from blizzard.runner.harness.workspace_prompts import IReadWorkspacePromptRepository, IWriteWorkspacePromptRepository
from blizzard.runner.transcripts.ledger import IReadTranscriptLedgerRepository, IWriteTranscriptLedgerRepository

__all__ = ["IReadRunnerStore", "IWriteRunnerStore", "RunnerStores"]


class IReadRunnerStore(
    IReadLeaseRepository,
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
    IWriteLeaseRepository,
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

    leases: IWriteLeaseRepository
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
