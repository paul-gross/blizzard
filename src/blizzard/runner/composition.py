"""The runner process and store composition root.

The only module under ``src/`` that names a concrete ``runner/store/internal/`` adapter,
asserted by
``tests/test_layering.py::test_composition_is_the_only_module_naming_a_concrete_runner_store_adapter``
— every other collaborator takes a Protocol seam or the
:class:`~blizzard.runner.stores.RunnerStores` bundle this builds.
Mirrors :func:`blizzard.hub.composition.build_services`."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from sqlalchemy import Engine

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import RunnerConfig
from blizzard.runner.environments.factory import build_workspace_provider
from blizzard.runner.environments.provider import IWorkspaceProvider
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID
from blizzard.runner.harness.internal.harness_registry import (
    build_production_harness_health_probes,
    build_production_harness_registry,
)
from blizzard.runner.harness.registry import HarnessRegistry
from blizzard.runner.loop.capability_snapshot import HarnessHealthCache, default_harness_id
from blizzard.runner.loop.process import LinuxProcessProbe
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
from blizzard.runner.store.internal.invocation_boundary_store import InvocationBoundaryStore
from blizzard.runner.store.internal.lease_liveness_store import LeaseLivenessStore
from blizzard.runner.store.internal.lease_record_store import LeaseRecordStore
from blizzard.runner.store.internal.lease_resume_intent_store import LeaseResumeIntentStore
from blizzard.runner.store.internal.lease_session_store import LeaseSessionStore
from blizzard.runner.store.internal.outbound_store import OutboundStore
from blizzard.runner.store.internal.overload_store import OverloadStore
from blizzard.runner.store.internal.pause_store import PauseStore
from blizzard.runner.store.internal.requeue_store import RequeueStore
from blizzard.runner.store.internal.selftest_result_store import SelfTestResultStore
from blizzard.runner.store.internal.takeover_store import TakeoverStore
from blizzard.runner.store.internal.token_store import TokenStore
from blizzard.runner.store.internal.transcript_ledger_store import TranscriptLedgerStore
from blizzard.runner.store.internal.usage_store import UsageStore
from blizzard.runner.store.internal.workspace_prompt_store import WorkspacePromptStore
from blizzard.runner.stores import RunnerReadStores, RunnerStores


@dataclass(frozen=True)
class RunnerProcess:
    """One owner for the hosted app, loop and recovery hooks' shared collaborators.

    Close after the loop has stopped and resume marking has drained workers: the
    spawner thread is the kernel parent of children awaiting durable confirmation.
    """

    engine: Engine
    stores: RunnerStores
    connections: RunnerStoreConnections
    provider: IWorkspaceProvider
    harnesses: HarnessRegistry
    clock: SystemClock
    process: LinuxProcessProbe
    health: HarnessHealthCache
    events: EventBroker | None
    executor: ThreadPoolExecutor

    def close(self) -> None:
        self.executor.shutdown(wait=True)
        self.engine.dispose()


def build_runner_process(config: RunnerConfig, *, events: EventBroker | None = None) -> RunnerProcess:
    """Construct the process-scoped graph; dispose partial resources on failure."""
    engine = create_engine_from_url(config.db_url)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="blizzard-spawner")
    try:
        stores, connections = build_stores_and_connections(
            engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store"))
        )
        clock = SystemClock()
        process = LinuxProcessProbe()
        provider = build_workspace_provider(config, held_ids=stores.environments.held_environment_ids)
        harnesses = build_production_harness_registry(config, executor=executor, process=process)
        default_id = default_harness_id(harnesses)
        if default_id is not None:
            harnesses.transcript_source(default_id)
        health = HarnessHealthCache(
            clock=clock,
            probes=build_production_harness_health_probes(config),
            selftest_results=stores.selftest_results,
            configured_tiers={
                CLAUDE_CODE_HARNESS_ID: config.model_aliases,
                OPENCODE_HARNESS_ID: config.opencode_model_aliases,
            },
        )
        return RunnerProcess(engine, stores, connections, provider, harnesses, clock, process, health, events, executor)
    except BaseException:
        executor.shutdown(wait=True)
        engine.dispose()
        raise


def build_stores(engine: Engine, *, errors: RunnerStoreErrorFactory) -> RunnerStores:
    """Construct and wire every extracted concept-store adapter over a migrated engine."""
    return _build_stores(RunnerStoreConnections(engine, errors))


def build_stores_and_connections(
    engine: Engine, *, errors: RunnerStoreErrorFactory
) -> tuple[RunnerStores, RunnerStoreConnections]:
    """Build the store bundle and hand back the ``RunnerStoreConnections`` every adapter in it
    shares — for the one caller (``build_hosted_app``) that wires a second ``store/internal/``-
    style collaborator (``JtiCacheRepository``) over the same engine, so it reuses this instance
    instead of building its own."""
    connections = RunnerStoreConnections(engine, errors)
    return _build_stores(connections), connections


def _build_stores(connections: RunnerStoreConnections) -> RunnerStores:
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
        overload=OverloadStore(connections),
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
        invocation_boundaries=InvocationBoundaryStore(connections),
        selftest_results=SelfTestResultStore(connections),
    )


def build_read_stores(engine: Engine, *, errors: RunnerStoreErrorFactory) -> RunnerReadStores:
    """The read-only bundle a controller-facing collaborator takes — narrows build_stores's
    bundle over the same adapter instances, never a second one."""
    return RunnerReadStores.of(build_stores(engine, errors=errors))
