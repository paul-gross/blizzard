"""The runner-store composition root (blizzard#410, D4).

The only module under ``src/`` that names a concrete ``runner/store/internal/`` adapter
(the structural gate Phase 4 adds asserts this) — every other collaborator takes a
Protocol seam or the :class:`~blizzard.runner.stores.RunnerStores` bundle this builds.
Mirrors :func:`blizzard.hub.composition.build_services`."""

from __future__ import annotations

from sqlalchemy import Engine

from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.store.internal.ask_store import AskStore
from blizzard.runner.store.internal.attachment_store import AttachmentStore
from blizzard.runner.store.internal.base import RunnerStoreConnections
from blizzard.runner.store.internal.check_store import CheckStore
from blizzard.runner.store.internal.elicitation_store import ElicitationStore
from blizzard.runner.store.internal.environment_store import EnvironmentStore
from blizzard.runner.store.internal.escalation_store import EscalationStore
from blizzard.runner.store.internal.git_commit_declaration_store import GitCommitDeclarationStore
from blizzard.runner.store.internal.graph_artifact_store import GraphArtifactStore
from blizzard.runner.store.internal.lease_liveness_store import LeaseLivenessStore
from blizzard.runner.store.internal.lease_record_store import LeaseRecordStore
from blizzard.runner.store.internal.lease_resume_intent_store import LeaseResumeIntentStore
from blizzard.runner.store.internal.lease_session_store import LeaseSessionStore
from blizzard.runner.store.internal.outbound_store import OutboundStore
from blizzard.runner.store.internal.pause_store import PauseStore
from blizzard.runner.store.internal.requeue_store import RequeueStore
from blizzard.runner.store.internal.takeover_store import TakeoverStore
from blizzard.runner.store.internal.token_store import TokenStore
from blizzard.runner.store.internal.transcript_ledger_store import TranscriptLedgerStore
from blizzard.runner.store.internal.usage_store import UsageStore
from blizzard.runner.store.internal.workspace_prompt_store import WorkspacePromptStore
from blizzard.runner.stores import RunnerReadStores, RunnerStores


def build_stores(engine: Engine, *, errors: RunnerStoreErrorFactory) -> RunnerStores:
    """Construct and wire every extracted concept-store adapter over a migrated engine."""
    connections = RunnerStoreConnections(engine, errors)
    return RunnerStores(
        lease_record=LeaseRecordStore(connections),
        session=LeaseSessionStore(connections),
        liveness=LeaseLivenessStore(connections),
        resume_intent=LeaseResumeIntentStore(connections),
        environments=EnvironmentStore(connections),
        transcript_ledger=TranscriptLedgerStore(connections),
        tokens=TokenStore(connections),
        workspace_prompt=WorkspacePromptStore(connections),
        outbound=OutboundStore(connections),
        asks=AskStore(connections),
        pause=PauseStore(connections),
        takeover=TakeoverStore(connections),
        requeue=RequeueStore(connections),
        escalations=EscalationStore(connections),
        usage=UsageStore(connections),
        attachments=AttachmentStore(connections),
        git_commit_declarations=GitCommitDeclarationStore(connections),
        checks=CheckStore(connections),
        graph_artifacts=GraphArtifactStore(connections),
        elicitations=ElicitationStore(connections),
    )


def build_read_stores(engine: Engine, *, errors: RunnerStoreErrorFactory) -> RunnerReadStores:
    """The read-only bundle a controller-facing collaborator takes — narrows build_stores's
    bundle over the same adapter instances, never a second one (D3)."""
    return RunnerReadStores.of(build_stores(engine, errors=errors))
