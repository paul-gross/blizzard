"""SQLAlchemy adapter for the runner-store repository (package-private).

The one place the runner's facts touch the engine (``bzh:pluggable-seams``). Composes
every extracted concept adapter (blizzard#410) — the whole ``IWriteRunnerStore`` surface,
none of it its own."""

from __future__ import annotations

from sqlalchemy import Engine

from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.store.internal.ask_store import AskStore
from blizzard.runner.store.internal.attachment_store import AttachmentStore
from blizzard.runner.store.internal.base import RunnerStoreConnections
from blizzard.runner.store.internal.check_store import CheckStore
from blizzard.runner.store.internal.environment_store import EnvironmentStore
from blizzard.runner.store.internal.escalation_store import EscalationStore
from blizzard.runner.store.internal.git_declaration_store import GitDeclarationStore
from blizzard.runner.store.internal.graph_artifact_store import GraphArtifactStore
from blizzard.runner.store.internal.lease_store import LeaseStore
from blizzard.runner.store.internal.outbound_store import OutboundStore
from blizzard.runner.store.internal.pause_store import PauseStore
from blizzard.runner.store.internal.requeue_store import RequeueStore
from blizzard.runner.store.internal.takeover_store import TakeoverStore
from blizzard.runner.store.internal.token_store import TokenStore
from blizzard.runner.store.internal.transcript_ledger_store import TranscriptLedgerStore
from blizzard.runner.store.internal.usage_store import UsageStore
from blizzard.runner.store.internal.workspace_prompt_store import WorkspacePromptStore
from blizzard.runner.stores import IWriteRunnerStore


class SqlAlchemyRunnerStore(
    LeaseStore,
    EnvironmentStore,
    TranscriptLedgerStore,
    TokenStore,
    WorkspacePromptStore,
    OutboundStore,
    AskStore,
    PauseStore,
    TakeoverStore,
    RequeueStore,
    EscalationStore,
    UsageStore,
    AttachmentStore,
    GitDeclarationStore,
    CheckStore,
    GraphArtifactStore,
):
    """Read-write runner store over a SQLAlchemy engine — every extracted concept seam
    (blizzard#410) composed by inheritance, so this class answers the whole
    ``IWriteRunnerStore`` surface."""

    def __init__(self, engine: Engine, errors: RunnerStoreErrorFactory) -> None:
        store = RunnerStoreConnections(engine, errors)
        LeaseStore.__init__(self, store)
        EnvironmentStore.__init__(self, store)
        TranscriptLedgerStore.__init__(self, store)
        TokenStore.__init__(self, store)
        WorkspacePromptStore.__init__(self, store)
        OutboundStore.__init__(self, store)
        AskStore.__init__(self, store)
        PauseStore.__init__(self, store)
        TakeoverStore.__init__(self, store)
        RequeueStore.__init__(self, store)
        EscalationStore.__init__(self, store)
        UsageStore.__init__(self, store)
        AttachmentStore.__init__(self, store)
        GitDeclarationStore.__init__(self, store)
        CheckStore.__init__(self, store)
        GraphArtifactStore.__init__(self, store)
        self._engine = engine
        self._errors = errors


def _conforms_runner_store(x: SqlAlchemyRunnerStore) -> IWriteRunnerStore:
    return x
