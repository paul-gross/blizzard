"""The runner host, runner process and store composition root.

One of the host's composition modules that may name a concrete ``runner/store/internal/`` adapter; every other
collaborator takes a Protocol seam or the :class:`~blizzard.runner.stores.RunnerStores` bundle this builds."""

from __future__ import annotations

import contextlib
import functools
import os
import socket
import threading
from collections.abc import Callable, Generator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import tzinfo
from pathlib import Path
from typing import Any, Protocol, cast

import httpx2
from sqlalchemy import Engine

from blizzard import __version__
from blizzard.foundation.clock import (
    IClock,
    IMonotonicClock,
    SystemClock,
    SystemMonotonicClock,
    resolve_clock,
)
from blizzard.foundation.flags import FlagRegistry, Flags, resolve_flags
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.handle import (
    DisabledPlatformTracing,
    IPlatformTracing,
    build_platform_tracing,
)
from blizzard.foundation.platform_tracing.tracer import IPlatformTracer
from blizzard.foundation.registry.interface import Lifetime, port
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import OwnedChild, ScopeValues, open_child, supply
from blizzard.foundation.roles import collaborator, domain_model
from blizzard.foundation.runner_start_step import RunnerStartStep
from blizzard.foundation.secrets.factories import secret_cipher
from blizzard.foundation.secrets.sealing import ISecretCipher, SealedValue
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.foundation.store.readiness import Readiness, ReadinessService
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.runner.config import (
    HostWorkspace,
    ProjectBinding,
    RunnerConfig,
    SubscriptionDeclaration,
    worker_shell_refusal,
)
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.config_table import ConfigError
from blizzard.runner.environments.factory import WorkspaceLaunch, build_workspace_provider
from blizzard.runner.environments.workspaces import (
    HostWorkspaces,
    IWorkspaceEntries,
    IWorkspaceSpawnRoots,
    WorkspaceEntry,
    WorkspaceLocks,
    WorkspaceRegistry,
)
from blizzard.runner.events.broker import EventBroker, RunnerEventPublisher
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.harness.binding_selection import BindingSelection, CachedVersionProbe, probed_binary
from blizzard.runner.harness.bundle import BundleSnapshot
from blizzard.runner.harness.capability_snapshot import BinaryVersionCache, HarnessVersionCache, default_harness_id
from blizzard.runner.harness.declaration import IVersionSelectedDeclaration
from blizzard.runner.harness.harness_set import HarnessAssemblyInputs, HarnessAssemblyKey, HarnessSet, HarnessSets
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryNames, HarnessTelemetryPlan
from blizzard.runner.harness.health_cache import HarnessHealthCache
from blizzard.runner.harness.internal.binary_identity import ToolBinaryIdentity
from blizzard.runner.harness.internal.bundle_publisher import bundle_source_identity
from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.internal.version_probe import VersionProbe
from blizzard.runner.harness.registry import HarnessRegistry
from blizzard.runner.harness.wiring import (
    HarnessSections,
    HarnessSettings,
    build_production_harnesses,
    bundle_composition_inputs,
    combined_telemetry_plan,
    configured_tiers,
    declared_telemetry_names,
    harness_families,
    publish_harness_bundle,
    select_harness_bindings,
)
from blizzard.runner.host_config import (
    GRADUATION_PROCEDURE,
    RUNNER_PATH_KEYS,
    UNSUFFIXED_TOKEN_VARIABLE,
    BuiltRunner,
    HaltedRunner,
    HostConfig,
    RunnerEntry,
    read_legacy_document,
)
from blizzard.runner.host_file import (
    HostFileEditor,
    bindings_literal,
    runner_table,
    runner_value,
    string_literal,
    with_runner_values,
)
from blizzard.runner.host_keys import ENV_HOST_SECRET_KEY, host_key_provider, sealing_key_provider
from blizzard.runner.host_layout import (
    HOST_CONFIG_FILENAME,
    HostLayout,
    hold_host,
)
from blizzard.runner.host_records.model import HostRecord, HostRecordsSnapshot, RecordKind
from blizzard.runner.host_records.prompt import PromptChange
from blizzard.runner.host_records.service import HostRecordService
from blizzard.runner.host_settings_reader import (
    HostRecordReads,
    HostRecordValidation,
    HostSettingsInForce,
    HostSettingsReader,
    HostSettingsSnapshot,
)
from blizzard.runner.hosting.names import RecordedName, hosted_name
from blizzard.runner.hosting.ports import IHostRunnerHub, IRunnerAdmission
from blizzard.runner.hosting.reconcile import RunnerStartFailure, RunnerStartFailures, reconcile
from blizzard.runner.hosting.retirement import HubNoticedRetirement, RetirementNotices
from blizzard.runner.hosting.roster import HostRoster, beside_the_file_of
from blizzard.runner.hosting.service import HostedRunnerService
from blizzard.runner.hub.client import HostReport, HostReportedWorkspace, IHostHubClient
from blizzard.runner.hub.client import SubscriptionDeclaration as HubSubscriptionDeclaration
from blizzard.runner.hub.flags import FlagsHolder
from blizzard.runner.hub.held_token import HubTokenHolder, IHubTokenSource
from blizzard.runner.hub.host_identity import HostIdentity, HostIdentityHolder
from blizzard.runner.hub.host_registration import HostRegistration, HostRegistrationHolder, ICurrentHostRegistration
from blizzard.runner.hub.hub_version import HubVersionHolder
from blizzard.runner.hub.identity import (
    ICurrentRunnerIdentity,
    IdentityVerdictHolder,
    IRunnerIdentityStore,
    RunnerIdentityHolder,
)
from blizzard.runner.hub.identity_service import RetiredRunnerWatch
from blizzard.runner.hub.internal.http_hub import HeldTokenAuth, HttpHostHubClient, HttpHubClient
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.hub.sealed_tokens import (
    HubTokens,
    IWriteHubTokenRepository,
    RunnerHubTokenSource,
    SealedHubToken,
    StoredToken,
    StoredTokenState,
)
from blizzard.runner.hub.served_projects import ServedProjectsAnswer
from blizzard.runner.lifecycle.registration import ISeedRunnerSettings
from blizzard.runner.listeners import Listeners
from blizzard.runner.loop.host_tick import HostTick, HostTickInForce
from blizzard.runner.loop.wake import WakeSignals
from blizzard.runner.process.child_env import ChildEnv, IToolProfile, ToolProfileHolder
from blizzard.runner.process.child_processes import IChildProcesses
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from blizzard.runner.runtime import (
    StoreResetRefused,
    runner_store_migrations,
    set_aside_sqlite,
    sqlite_store_file,
    sqlite_url,
)
from blizzard.runner.settings.authoring import HostSettingsApplication, HostSettingsAuthoring
from blizzard.runner.settings.ports import IWriteHostSettings, IWriteRunnerSettings
from blizzard.runner.settings_reader import HostApplyJudge, RunnerEntriesJudge, RunnerSettingsReader
from blizzard.runner.status.invariants import HostInvariantCheck
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.store.internal.ask_store import AskStore
from blizzard.runner.store.internal.attachment_store import AttachmentStore
from blizzard.runner.store.internal.check_store import CheckStore
from blizzard.runner.store.internal.elicitation_store import ElicitationStore
from blizzard.runner.store.internal.environment_store import EnvironmentStore
from blizzard.runner.store.internal.escalation_store import EscalationStore
from blizzard.runner.store.internal.execution_observation_store import ExecutionObservationStore
from blizzard.runner.store.internal.git_commit_declaration_store import GitCommitDeclarationStore
from blizzard.runner.store.internal.host_external_usage_store import ExternalUsageStore
from blizzard.runner.store.internal.host_hosted_runner_store import HostHostedRunnerStore
from blizzard.runner.store.internal.host_hub_token_store import HostHubTokenStore
from blizzard.runner.store.internal.host_record_store import HostSubscriptionStore, HostWorkspaceStore
from blizzard.runner.store.internal.host_runner_audit import RunnerAudit
from blizzard.runner.store.internal.host_settings_apply_store import HostSettingsApplyStore
from blizzard.runner.store.internal.host_settings_store import HostSettingsStore
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from blizzard.runner.store.internal.invocation_boundary_store import InvocationBoundaryStore
from blizzard.runner.store.internal.lease_liveness_store import LeaseLivenessStore
from blizzard.runner.store.internal.lease_lock_store import LeaseLockStore
from blizzard.runner.store.internal.lease_record_store import LeaseRecordStore
from blizzard.runner.store.internal.lease_resume_intent_store import LeaseResumeIntentStore
from blizzard.runner.store.internal.lease_session_store import LeaseSessionStore
from blizzard.runner.store.internal.lease_trace_facts_store import LeaseTraceFactsStore
from blizzard.runner.store.internal.outbound_store import OutboundStore
from blizzard.runner.store.internal.overload_store import OverloadStore
from blizzard.runner.store.internal.pause_store import PauseStore
from blizzard.runner.store.internal.projects_answer_store import ServedProjectsStore
from blizzard.runner.store.internal.requeue_store import RequeueStore
from blizzard.runner.store.internal.runner_admission_store import RunnerAdmissionStore
from blizzard.runner.store.internal.runner_transfer_store import RunnerRowRemovalStore, RunnerTransferStore
from blizzard.runner.store.internal.selftest_result_store import SelfTestResultStore
from blizzard.runner.store.internal.settings_store import RunnerSettingsStore
from blizzard.runner.store.internal.takeover_store import TakeoverStore
from blizzard.runner.store.internal.token_store import TokenStore
from blizzard.runner.store.internal.transcript_ledger_store import TranscriptLedgerStore
from blizzard.runner.store.internal.usage_limit_store import RunnerUsageLimitStore
from blizzard.runner.store.internal.usage_store import UsageStore
from blizzard.runner.store.runner_guard import install_runner_guard
from blizzard.runner.store.scoped import RunnerStoreConnections
from blizzard.runner.store.wiring import build_host_stores
from blizzard.runner.store.wiring import host_stores as _build_host_stores
from blizzard.runner.stores import HostReadStores, HostStores, IRunnerAudit, RunnerReadStores, RunnerStores
from blizzard.runner.subscriptions.internal.credential_renewer_factory import select_renewer
from blizzard.runner.subscriptions.internal.subprocess_one_shot_process import SubprocessOneShotProcess
from blizzard.runner.subscriptions.internal.subscription_sampler_factory import select_sampler
from blizzard.runner.subscriptions.internal.usage_http_client import LazyUsageHttpClient
from blizzard.runner.throttle.resample import FloorResampleTriggers
from blizzard.runner.tracing.attributes import resource_attributes
from blizzard.runner.tracing.internal.runner_export import RunnerTraceExportFactory
from blizzard.runner.tracing.platform import (
    PLATFORM_INSTRUMENTATION_SCOPE,
    PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
    host_stamp,
    identity_stamp,
)
from blizzard.runner.tracing.received_export import (
    DisabledReceivedTelemetryExport,
    IReceivedTelemetryExport,
    build_received_telemetry_export,
)
from blizzard.runner.tracing.receiver_limits import ReceiverBounds, ReceiverCounter, SpanRateLimiter
from blizzard.runner.tracing.receiving import ReceiverTracing, ReceiverTracingInForce, TelemetryReceiver
from blizzard.runner.tracing.replay import LeaseTraceReplay
from blizzard.runner.tracing.runner_export import RunnerTraceExport, RunnerTraceOptions
from blizzard.runner.tracing.sweep import LeaseTraceSweep, announce_rejected_tracing
from blizzard.runner.transfer.host_files import IHostFiles
from blizzard.runner.transfer.hosts import (
    DeclaredBinding,
    DeclaredSubscription,
    DeclaredWorkspace,
    HeldRecords,
    HostDeclarations,
    IngestHost,
    ISourceHosts,
    RunnerDeclaration,
    SourceHost,
)
from blizzard.runner.transfer.ingest import IHereRecords, IHostCiphers, IIngestStores, ITokenFiles, RunnerIngest
from blizzard.runner.transfer.internal.host_files import HostFiles
from blizzard.runner.transfer.refusals import SourceUnsupported
from blizzard.runner.transfer.rows import HostIngest, IRunnerRowRemoval, IRunnerTransfer
from blizzard.runner.transfer.runner_stores import HeldBinding, IRunnerBoundStores, RunnerStoreView
from blizzard.runner.transfer.source import SQLITE_HEADER, DirectoryState, SourceKind, classify_source
from blizzard.runner.usage.credential_renewal import CredentialRenewalPass, RenewableSubscription
from blizzard.runner.usage.external_usage_sample import (
    ExternalUsageAttemptLane,
    ExternalUsageSamplePass,
    IAttemptLaneRunner,
    SampledSubscription,
)
from blizzard.runner.usage.repository import IWriteExternalUsageAttemptRepository


def _receiver_tracing(host: HostConfig | RunnerConfig) -> ReceiverTracing:
    return ReceiverTracing(
        worker_programs=host.tracing.worker_programs,
        harness_telemetry=host.tracing.harness_telemetry,
        mapped_services=host.tracing.worker_program_services,
    )


@dataclass(frozen=True)
class SeedingStore:
    """The open engine and host stores a hosted runner is seeded over, its opener's to dispose, and the sealed tokens
    its token is imported into."""

    engine: Engine
    host_stores: HostStores
    tokens: Callable[[], HubTokens]


class HostRunnerRoster:
    """The runners currently held by a host, read by host-scoped passes."""

    def __init__(self) -> None:
        self._runners: tuple[RunnerProcess, ...] = ()

    def current(self) -> tuple[RunnerProcess, ...]:
        return self._runners

    def hold(self, runners: tuple[RunnerProcess, ...]) -> None:
        self._runners = runners


@dataclass
class HostProcess:
    """The per-host graph: the pieces one daemon shares across every runner it hosts, and the runners themselves.

    The host reaches a runner only through :attr:`runners`; a runner reaches the host it was built over. Stop every
    serving thread before closing; runner graphs close before the process scope and its owned resources.
    """

    engine: Engine
    readiness: ReadinessService
    host_stores: HostStores
    listeners: Listeners
    #: The one holder of the host's workspace registry in force, and of the pool locks that outlive each rebuild.
    workspaces: HostWorkspaces
    events: EventBroker | None
    clock: IClock
    #: What every runner on the host measures its harnesses' deadlines and delays on — a time source, holding no state.
    monotonic_clock: IMonotonicClock
    #: The host's local zone, with its daylight-saving rules, that the schedule lever reads the wall clock in.
    zone: tzinfo
    process: LinuxProcessProbe
    #: The one launch seam every child of the host and its runners goes through.
    children: IChildProcesses
    #: The worker telemetry receiver over the host's limiters, tallies and export — one per host, so every reader and
    #: writer of those counters shares them; each export names the runner it arrived under.
    telemetry_receiver: TelemetryReceiver
    #: The current host pass and subscriptions in force.
    tick_in_force: HostTickInForce[SubscriptionDeclaration]
    #: The runner half of the invariant checker over every runner on the host, one run at a time.
    invariants: HostInvariantCheck
    #: The one HTTP client every declared subscription's sampler shares, built on first use and closed with the host.
    usage_http_client: LazyUsageHttpClient
    #: Every flag the fleet declares, and the host's own resolved answer — what its ``GET /api/flags`` publishes.
    flag_registry: FlagRegistry
    flags: Flags
    infrastructure: OwnedChild = field(repr=False)
    #: Platform spans — disabled unless a handle is supplied; every collaborator opens spans through it.
    platform_tracing: IPlatformTracing = field(default_factory=DisabledPlatformTracing)
    #: Where admitted metrics and logs leave — disabled unless a handle is supplied.
    received_telemetry: IReceivedTelemetryExport = field(default_factory=DisabledReceivedTelemetryExport)
    runner_roster: HostRunnerRoster = field(default_factory=HostRunnerRoster)
    #: The runners the host file declares that were left unbuilt by their own errors; the host serves without them.
    halted: tuple[HaltedRunner, ...] = ()
    #: The host tick's per-pass reconcile of its workspaces and subscriptions; ``None`` for a host keeping no records.
    records_reconcile: HostRecordsReconcile | None = None
    #: Who this host is at its hub; seeded from the store at boot, and handed the identity once it is first recorded.
    host_identity: HostIdentityHolder = field(default_factory=HostIdentityHolder)
    #: The host registration step's latest outcome — unregistered on a host with no host token.
    host_registration: HostRegistrationHolder = field(
        default_factory=lambda: HostRegistrationHolder.for_host(registered=False)
    )
    #: The host's own hub calls over that client — ``None`` on a host with no host token.
    host_hub: IHostHubClient | None = None
    #: The tool profile every host tool child launches under — the workspaces' ``git`` and ``winter``, credential
    #: renewal, the selftest's scratch ``git`` — set by the host tick from its snapshot.
    tool_profile: ToolProfileHolder = field(default_factory=lambda: ToolProfileHolder(ChildEnv(())))
    #: The host record in force, which the host tick snapshots first; ``None`` for a host built from no host file.
    host_settings: HostSettingsInForce | None = None
    #: The host record's one write path, which wakes :attr:`wakes` after a write moving an interval; ``None`` beside
    #: an absent :attr:`host_settings`.
    host_settings_authoring: HostSettingsAuthoring | None = None
    #: The host settings document's apply and export, which wakes :attr:`wakes` after an apply moving an interval;
    #: ``None`` beside an absent :attr:`host_settings`.
    host_settings_application: HostSettingsApplication | None = None
    #: Every timed driver's wake signal; a host write moving an interval wakes them all.
    wakes: WakeSignals = field(default_factory=WakeSignals)
    #: Each runner's sealed hub token under the host's key — ``None`` on a host that holds no key yet.
    hub_tokens: HubTokens | None = None
    #: Whether the host tick's registration step recorded a runner retired, so the same pass drains it.
    retirements: RetirementNotices = field(default_factory=RetirementNotices)
    root_managed: bool = field(default=False, repr=False)
    infrastructure_transferred: bool = field(default=False, repr=False)

    def registered(self) -> bool:
        """Whether the host holds a host token: it reports itself, and its roster with it, so its runners declare
        no subscriptions of their own."""
        return self.host_registration.current().registered()

    @property
    def runners(self) -> tuple[RunnerProcess, ...]:
        return self.runner_roster.current()

    @runners.setter
    def runners(self, runners: tuple[RunnerProcess, ...]) -> None:
        self.runner_roster.hold(runners)

    def halted_runners(self) -> tuple[HaltedRunner, ...]:
        """The runners halted now — read on each call, since a host's runner set changes while it runs."""
        return self.halted

    def close(self) -> None:
        if self.infrastructure_transferred:
            return
        with contextlib.ExitStack() as closing:
            closing.callback(self.infrastructure.close)
            if not self.root_managed:
                for runner in self.runners:
                    closing.callback(runner.close_runner)


@dataclass(frozen=True)
class RunnerProcess:
    """One runner's own graph over a :class:`HostProcess`: its bound stores, identity, harnesses and spawner.

    Close after the loop has stopped and resume marking has drained workers: the spawner thread is the kernel parent
    of children awaiting durable confirmation. The host's engine outlives it.
    """

    host: HostProcess
    #: The configuration this runner was built from.
    config: RunnerConfig
    #: This runner's connections over the host's engine, bound to the runner it is.
    connections: RunnerStoreConnections
    stores: RunnerStores
    #: The one host-kind write a runner holds, over ``connections``: an unregistered host's sampled attempt, whose
    #: outbound report is this runner's row.
    external_usage_attempts: IWriteExternalUsageAttemptRepository
    harness_sets: HarnessSets
    executor: ThreadPoolExecutor
    #: Whether OpenTelemetry's own variables enable lease tracing, read once for this runner.
    trace_settings: TracingSettings
    #: The runner-owned trace-export binding, or the borrowed export's non-owning handle.
    trace_export: RunnerTraceExport
    #: The lease-trace sweep — ``None`` unless tracing is enabled.
    trace_sweep: LeaseTraceSweep | None
    #: The operator's replay over the same assembly and exporter.
    trace_replay: LeaseTraceReplay
    #: Who this runner is at its hub; seeded from the store at boot and refreshed by every registration.
    identity: RunnerIdentityHolder
    #: Whether this runner has verified its token names the configured runner — unchecked from start.
    identity_verdict: IdentityVerdictHolder
    #: Records this runner's retirement once its own token is refused as retired — the host tick's.
    retirement: RetiredRunnerWatch
    #: The token every runner->hub client presents — taken up again from the store whenever its row's revision moves.
    hub_token: HubTokenHolder
    #: The one client this runner's identity reads go through, under ``hub_token``; closed with the runner.
    identity_client: httpx2.Client
    #: The host's broker, stamping this runner's id on every runner-scoped event; ``None`` on a host with none.
    events: IRunnerEventPublisher | None
    #: This runner's own resolved flags: its ``runner`` flags at the host's overrides, and its ``fleet`` flags as its
    #: hub last answered them.
    flags: FlagsHolder = field(default_factory=lambda: FlagsHolder(resolve_flags(FlagRegistry(), "runner", {}).flags))
    #: The hub's version and window, as this runner's latest registration reply answered them.
    hub_version: HubVersionHolder = field(default_factory=HubVersionHolder)
    #: What a standalone opening closes after this runner's graph; ``None`` when a host's runner set closes it.
    closes: Callable[[], None] | None = None
    settings_reader: RunnerSettingsReader | None = None
    #: The seed of this runner's settings record, built beside ``settings_reader`` over the same entry.
    settings_seed: ISeedRunnerSettings | None = None
    harness_key: Callable[[RunnerConfig], HarnessAssemblyKey] | None = None

    @property
    def harnesses(self) -> HarnessRegistry:
        return self.harness_sets.current.registry

    @property
    def health(self) -> HarnessHealthCache:
        return self.harness_sets.current.health

    @property
    def harness_telemetry(self) -> HarnessTelemetryPlan:
        return self.harness_sets.current.telemetry

    @property
    def runner_id(self) -> str:
        """The hub id this runner is configured with, which its stores are bound to."""
        return self.connections.bound_runner_id()

    @property
    def engine(self) -> Engine:
        return self.host.engine

    @property
    def workspaces(self) -> HostWorkspaces:
        return self.host.workspaces

    @property
    def clock(self) -> IClock:
        return self.host.clock

    @property
    def process(self) -> LinuxProcessProbe:
        return self.host.process

    @property
    def children(self) -> IChildProcesses:
        return self.host.children

    @property
    def platform_tracing(self) -> IPlatformTracing:
        return self.host.platform_tracing

    @property
    def tracer(self) -> IPlatformTracer:
        """The host's platform tracer, each span it opens — and every span opened under one — stamped as this
        runner."""
        return self.host.platform_tracing.stamped(identity_stamp(self.identity))

    @property
    def received_telemetry(self) -> IReceivedTelemetryExport:
        return self.host.received_telemetry

    @property
    def telemetry_receiver(self) -> TelemetryReceiver:
        return self.host.telemetry_receiver

    @property
    def span_limiter(self) -> SpanRateLimiter:
        return self.host.telemetry_receiver.span_limiter

    @property
    def receiver_counter(self) -> ReceiverCounter:
        return self.host.telemetry_receiver.span_counter

    @property
    def harness_span_counter(self) -> ReceiverCounter:
        return self.host.telemetry_receiver.harness_span_counter

    @property
    def metric_bounds(self) -> ReceiverBounds:
        return self.host.telemetry_receiver.metric_bounds

    @property
    def log_bounds(self) -> ReceiverBounds:
        return self.host.telemetry_receiver.log_bounds

    @property
    def harness_telemetry_names(self) -> tuple[HarnessTelemetryNames, ...]:
        return self.host.telemetry_receiver.telemetry_names

    def close_runner(self) -> None:
        """Shut the spawner executor down, waiting for its running work, then close the identity client. Idempotent."""
        try:
            self.executor.shutdown(wait=True)
        finally:
            try:
                self.identity_client.close()
            finally:
                try:
                    self.harness_sets.clear()
                finally:
                    self.trace_export.close()

    def close(self) -> None:
        try:
            self.close_runner()
        finally:
            if self.closes is not None:
                self.closes()


@dataclass(frozen=True)
class HostReadiness(ReadinessService):
    """The store's readiness with the host's halted runners named in its detail. ``ready`` keeps meaning the store's: a
    halted runner is the host's to report, not a reason it cannot serve."""

    #: The host's halted runners as each evaluation finds them, so a runner whose retried start succeeds stops being
    #: named and one whose later start fails starts being.
    halted: Callable[[], Sequence[HaltedRunner]] = tuple

    def evaluate(self) -> Readiness:
        readiness = super().evaluate()
        halted = [runner.describe() for runner in self.halted()]
        if not halted:
            return readiness
        detail = "; ".join(filter(None, (readiness.detail, *halted)))
        return replace(readiness, detail=detail)


def pool_held_ids(audit: IRunnerAudit, workspace_id: str) -> Callable[[], list[str]]:
    """The environment ids any of the host's runners holds in ``workspace_id``'s pool, or under no workspace."""

    def held() -> list[str]:
        return list(dict.fromkeys(b.environment_id for b in audit.held_workspace_bindings(workspace_id)))

    return held


def build_workspaces(
    declared: Sequence[HostWorkspace],
    host_root: Path,
    *,
    launch: WorkspaceLaunch,
    audit: IRunnerAudit | None = None,
    locks: WorkspaceLocks | None = None,
    revisions: Mapping[str, int] | None = None,
    keep: WorkspaceRegistry | None = None,
    retired: Sequence[HostWorkspace] = (),
) -> WorkspaceRegistry:
    """The host's workspace registry over ``locks``: one provider per declared workspace, launching through
    ``launch``, its spawn root and standing prompt resolved now — an unresolvable prompt raises. With ``audit``, each
    provider reads its pool's held set host-wide. A workspace ``keep`` holds at its ``revisions`` entry keeps its
    provider; ``retired`` ones serve only what they hold."""
    revisions = revisions or {}

    def entry(workspace: HostWorkspace) -> WorkspaceEntry:
        revision = revisions.get(workspace.id)
        kept = keep.entry(workspace.id) if keep is not None else None
        if kept is not None and revision is not None and kept.revision == revision:
            return kept
        held_ids = pool_held_ids(audit, workspace.id) if audit is not None else None
        provider = build_workspace_provider(workspace.settings(host_root), launch=launch, held_ids=held_ids)
        return WorkspaceEntry(
            workspace.id,
            provider,
            spawn_root=provider.spawn_root(),
            prompt=workspace.resolved_prompt(host_root),
            max_environments=workspace.max_environments,
            revision=revision,
        )

    return WorkspaceRegistry(
        [entry(workspace) for workspace in declared],
        retired=[entry(workspace) for workspace in retired],
        locks=locks,
    )


def bound_spawn_roots(config: RunnerConfig, workspaces: IWorkspaceSpawnRoots) -> tuple[str, ...]:
    """The distinct spawn roots of the workspaces ``config``'s runner binds — what its health probes and
    ambient-conflict checks run over."""
    return tuple(dict.fromkeys(workspaces.spawn_root(workspace.id) for workspace in config.bound_workspaces))


def renewable_subscription(
    declaration: SubscriptionDeclaration, clock: IClock, *, subprocess: SubprocessOneShotProcess, env: IToolProfile
) -> RenewableSubscription | None:
    """``declaration`` with its provider's renewer binding; ``None`` when its provider binds none."""
    renewer = select_renewer(declaration, clock=clock, subprocess=subprocess, env=env)
    if renewer is None:
        return None
    return RenewableSubscription(
        slug=declaration.slug, sample_interval_seconds=declaration.sample_interval_seconds, renewer=renewer
    )


def credential_renewal_pass(
    declarations: Sequence[SubscriptionDeclaration],
    host_stores: HostStores,
    clock: IClock,
    *,
    children: IChildProcesses,
    env: IToolProfile,
) -> CredentialRenewalPass | None:
    """Every subscription the host declares paired with its provider's renewer binding, sharing one
    one-shot subprocess seam over ``children`` under the tool profile ``env``; ``None`` when no provider binds
    one."""
    subprocess = SubprocessOneShotProcess(children)
    renewable = [
        renewable
        for declaration in declarations
        if (renewable := renewable_subscription(declaration, clock, subprocess=subprocess, env=env)) is not None
    ]
    return _renewal_pass(renewable, host_stores, clock)


def _renewal_pass(
    renewable: Sequence[RenewableSubscription], host_stores: HostStores, clock: IClock
) -> CredentialRenewalPass | None:
    if not renewable:
        return None
    return CredentialRenewalPass(subscriptions=renewable, renewals=host_stores.external_usage, clock=clock)


def sampled_subscription(
    declaration: SubscriptionDeclaration, clock: IClock, http_client: LazyUsageHttpClient
) -> SampledSubscription:
    """``declaration`` with its provider's sampler, sharing the host's one usage HTTP client."""
    return SampledSubscription(
        slug=declaration.slug,
        name=declaration.name,
        sample_interval_seconds=declaration.sample_interval_seconds,
        sampler=select_sampler(declaration, clock=clock, http_client=http_client),
    )


def _external_usage_sample_pass(
    declarations: Sequence[SubscriptionDeclaration],
    host_stores: HostStores,
    clock: IClock,
    http_client: LazyUsageHttpClient,
    runners: Callable[[], Sequence[IAttemptLaneRunner]],
    *,
    registration: ICurrentHostRegistration,
) -> ExternalUsageSamplePass | None:
    """Every subscription the host declares paired with its provider's sampler, sharing one HTTP client; ``None``
    when the host declares none. ``registration`` says whether the host holds a host token, so its attempts ride its
    own lane."""
    sampled = [sampled_subscription(declaration, clock, http_client) for declaration in declarations]
    return _sample_pass(sampled, host_stores, clock, runners, registration=registration)


def _sample_pass(
    sampled: Sequence[SampledSubscription],
    host_stores: HostStores,
    clock: IClock,
    runners: Callable[[], Sequence[IAttemptLaneRunner]],
    *,
    registration: ICurrentHostRegistration,
) -> ExternalUsageSamplePass | None:
    if not sampled:
        return None
    return ExternalUsageSamplePass(
        subscriptions=sampled,
        attempts=host_stores.external_usage,
        early=FloorResampleTriggers(host_stores.usage_limits, host_stores.external_usage),
        sink=ExternalUsageAttemptLane(host_stores.host_lane, runners, registration=registration),
        clock=clock,
    )


class HostRecordsReconcile:
    """The host tick's reconcile of its workspaces and subscriptions against the pass's snapshot of their records:
    a moved workspace record rebuilds the registry in one assignment, unmoved workspaces keeping their providers; a
    moved subscription rebuilds its sampler and renewer. A rebuild that raises keeps what is in force, and the next
    pass tries again."""

    def __init__(
        self,
        root: Path,
        *,
        workspaces: HostWorkspaces,
        children: IChildProcesses,
        tool_profile: ToolProfileHolder,
        stores: HostStores,
        clock: IClock,
        usage_http_client: LazyUsageHttpClient,
        tick_in_force: HostTickInForce[SubscriptionDeclaration],
        runners: Callable[[], Sequence[IAttemptLaneRunner]],
        registration: ICurrentHostRegistration,
        retired_workspaces: Callable[[HostSettingsSnapshot], tuple[HostWorkspace, ...]],
    ) -> None:
        self._root = root
        self._workspaces = workspaces
        self._children = children
        self._tool_profile = tool_profile
        self._stores = stores
        self._clock = clock
        self._usage_http_client = usage_http_client
        self._tick_in_force = tick_in_force
        self._runners = runners
        self._registration = registration
        self._retired_workspaces = retired_workspaces
        self._log = get_logger("blizzard.runner.loop")
        self._workspaces_at: tuple[tuple[str, int], ...] | None = None
        self._subscriptions_at: dict[str, int] | None = None
        self._sampled: dict[str, SampledSubscription] = {}
        self._renewable: dict[str, RenewableSubscription | None] = {}

    def run(self, effective: HostConfig, snapshot: HostSettingsSnapshot) -> None:
        records = snapshot.records
        if records is None:
            return
        try:
            self._reconcile_workspaces(effective, snapshot, records)
        except Exception:  # a prompt file gone, a provider that will not build: keep the registry in force
            self._log.warning("workspace rebuild failed — the host keeps its workspaces", exc_info=True)
        try:
            self._reconcile_subscriptions(effective, records)
        except Exception:
            self._log.warning("subscription rebuild failed — the host keeps its subscriptions", exc_info=True)

    def _reconcile_workspaces(
        self, effective: HostConfig, snapshot: HostSettingsSnapshot, records: HostRecordsSnapshot
    ) -> None:
        at = tuple((record.key, record.revision) for record in (*records.workspaces, *records.retired_workspaces))
        if at == self._workspaces_at:
            return
        current = self._workspaces.current()
        registry = build_workspaces(
            effective.workspaces,
            self._root,
            launch=WorkspaceLaunch(children=self._children, env=self._tool_profile),
            audit=self._stores.runner_audit,
            locks=self._workspaces.locks,
            revisions=dict(at),
            # The registry the host was composed with carries no record revisions, so the first pass builds each anew.
            keep=current if self._workspaces_at is not None else None,
            retired=self._retired_workspaces(snapshot),
        )
        self._workspaces.set(registry)
        if self._workspaces_at is not None:
            self._log.info("host workspaces rebuilt", workspaces=list(registry.ids()))
        self._workspaces_at = at

    def _reconcile_subscriptions(self, effective: HostConfig, records: HostRecordsSnapshot) -> None:
        at = {record.key: record.revision for record in records.subscriptions}
        if at == self._subscriptions_at:
            return
        declared = {declaration.slug: declaration for declaration in effective.subscriptions}
        subprocess = SubprocessOneShotProcess(self._children)
        sampled: dict[str, SampledSubscription] = {}
        renewable: dict[str, RenewableSubscription | None] = {}
        for slug, declaration in declared.items():
            if (
                slug in self._sampled
                and self._subscriptions_at is not None
                and self._subscriptions_at.get(slug) == at.get(slug)
            ):
                sampled[slug], renewable[slug] = self._sampled[slug], self._renewable.get(slug)
                continue
            sampled[slug] = sampled_subscription(declaration, self._clock, self._usage_http_client)
            renewable[slug] = renewable_subscription(
                declaration, self._clock, subprocess=subprocess, env=self._tool_profile
            )
        first = self._subscriptions_at is None
        old_tick, old_subscriptions = self._tick_in_force.snapshot()
        if first and tuple(declared) == tuple(s.slug for s in old_subscriptions):
            # The pass the host was composed with already serves these declarations.
            self._sampled, self._renewable, self._subscriptions_at = sampled, renewable, at
            return
        tick = replace(
            old_tick,
            external_usage_sample=_sample_pass(
                tuple(sampled.values()),
                self._stores,
                self._clock,
                self._runners,
                registration=self._registration,
            ),
            credential_renewal=_renewal_pass(
                tuple(r for r in renewable.values() if r is not None), self._stores, self._clock
            ),
        )
        self._tick_in_force.hold(tick, tuple(declared.values()))
        self._sampled, self._renewable, self._subscriptions_at = sampled, renewable, at
        self._log.info("host subscriptions rebuilt", subscriptions=list(declared))


#: How long the runner's shutdown waits for buffered platform spans to leave.
PLATFORM_TRACING_SHUTDOWN_SECONDS = 3.0


def build_runner_platform_tracing(
    config: RunnerConfig, environ: Mapping[str, str] | None = None, *, identity: ICurrentRunnerIdentity | None = None
) -> IPlatformTracing:
    """The handle ``[tracing]`` and the OTLP environment decide. With ``identity``, every span it records carries
    the id and name ``identity`` answers as it starts, and none is exported before that runner's first
    registration; without, a span carries the runner stamp of what opened it or of its parent — a runner's tick
    tracer, a request under a runner's prefix — and one with neither, the host's own, carries no runner identity."""
    env = os.environ if environ is None else environ
    return build_platform_tracing(
        config.tracing,
        env,
        resource=resource_attributes(env, __version__),
        scope=PLATFORM_INSTRUMENTATION_SCOPE,
        scope_version=PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
        stamp=identity_stamp(identity) if identity is not None else host_stamp(),
    )


def build_runner_received_telemetry(
    config: RunnerConfig, monotonic_clock: IMonotonicClock, environ: Mapping[str, str] | None = None
) -> IReceivedTelemetryExport:
    """The handle Claude Code's received metrics and logs leave through: off unless ``[tracing]
    harness_telemetry`` and platform tracing are both on."""
    env = os.environ if environ is None else environ
    return build_received_telemetry_export(
        config.tracing, env, resource=resource_attributes(env, __version__), monotonic_clock=monotonic_clock
    )


def held_hub_token(
    config: RunnerConfig, token: StoredToken | None = None, source: IHubTokenSource | None = None
) -> HubTokenHolder:
    """The token the runner started with — its stored-token outcome, else the row ``source`` holds now, else
    ``config``'s token — taken up again from ``source`` whenever the row's revision moves."""
    if token is not None and token.state is not StoredTokenState.ABSENT:
        return HubTokenHolder.of(token, source)
    held = HubTokenHolder(config.hub_token, source=source)
    held.refresh()
    return held


def _hub_token_store(engine: Engine) -> HostHubTokenStore:
    return HostHubTokenStore(
        RunnerStoreConnections(engine, RunnerStoreErrorFactory(get_logger("blizzard.runner.store")))
    )


def build_hub_tokens(
    repository: IWriteHubTokenRepository, root: Path, *, environ: Mapping[str, str], clock: IClock
) -> HubTokens | None:
    """The host's sealed runner tokens under its key, never minted: ``None`` when the host holds no key yet. A
    malformed key variable raises :class:`ConfigError`."""
    keys = host_key_provider(environ, HostLayout(root), mint=False)
    return None if keys is None else HubTokens(repository, secret_cipher(keys), clock)


def build_sealing_hub_tokens(engine: Engine, root: Path, *, environ: Mapping[str, str], clock: IClock) -> HubTokens:
    """The host's sealed runner tokens under the key it seals under, its first generation minted when it holds none;
    refuses as :func:`~blizzard.runner.host_keys.sealing_key_provider` does."""
    return _sealing_hub_tokens(_hub_token_store(engine), root, environ=environ, clock=clock)


def _sealing_hub_tokens(
    repository: IWriteHubTokenRepository, root: Path, *, environ: Mapping[str, str], clock: IClock
) -> HubTokens:
    keys = sealing_key_provider(environ, HostLayout(root), sealed_under=repository.key_ids())
    return HubTokens(repository, secret_cipher(keys), clock)


def build_seeding_store(
    engine: Engine, root: Path, *, errors: RunnerStoreErrorFactory, environ: Mapping[str, str], clock: IClock
) -> SeedingStore:
    """The seeding store of a stopped-host start over ``engine``, its tokens sealed as
    :func:`build_sealing_hub_tokens` seals."""
    stores = build_host_stores(engine, errors=errors, clock=clock)
    tokens = functools.partial(_sealing_hub_tokens, stores.hub_tokens, root, environ=environ, clock=clock)
    return SeedingStore(engine, stores, tokens)


def build_running_host_seeding_store(host: HostProcess, root: Path, *, environ: Mapping[str, str]) -> SeedingStore:
    """The running ``host``'s seeding store: its own engine, host stores and token sealer."""
    return SeedingStore(
        host.engine, host.host_stores, functools.partial(_running_host_tokens, host, root, environ=environ)
    )


def stored_hub_token(engine: Engine, root: Path, runner_id: str, *, environ: Mapping[str, str]) -> StoredToken:
    """``runner_id``'s stored-token outcome, read without minting the host's key: a row with no key to open it is
    unreadable. A malformed key variable raises :class:`ConfigError`."""
    keys = host_key_provider(environ, HostLayout(root), mint=False)
    return StoredToken.of(_hub_token_store(engine).sealed(runner_id), None if keys is None else secret_cipher(keys))


@dataclass(frozen=True)
class RunnerCompose:
    """What a host composes each runner child with beyond its :class:`BuiltRunner`, for the host's lifetime; the
    per-runner options are keyed by runner id."""

    #: The runners whose harness bundle is published — the boot's; a later reconcile's start publishes none.
    publish: frozenset[str] = frozenset()
    bundles: Mapping[str, BundleSnapshot | None] = field(default_factory=dict[str, BundleSnapshot | None])
    selections: Mapping[str, BindingSelection] = field(default_factory=dict[str, BindingSelection])
    #: Decides whether tracing is enabled.
    environ: Mapping[str, str] | None = None
    #: Replaces the OTLP binding an enabled sweep would build.
    trace_exporter: ITraceExporter | None = None
    #: Whether each runner gets its settings reader; a standalone one's settings-reading verb attaches its own.
    settings: bool = True

    def runner(
        self,
        host_config: HostConfig,
        host: HostProcess,
        built: BuiltRunner,
        *,
        runner_trace_export: RunnerTraceExport | None = None,
    ) -> RunnerProcess:
        """``built`` composed over ``host`` under these options."""
        return compose_runner(
            host_config,
            host,
            built,
            bundle=self.bundles.get(built.id),
            selection=self.selections.get(built.id),
            publish_bundle=built.id in self.publish,
            environ=self.environ,
            trace_exporter=self.trace_exporter,
            runner_trace_export=runner_trace_export,
            settings=self.settings,
        )


def build_host_process(
    host: HostConfig,
    *,
    halted: Sequence[HaltedRunner] = (),
    events: EventBroker | None = None,
    environ: Mapping[str, str] | None = None,
    platform_tracing: IPlatformTracing | None = None,
    received_telemetry: IReceivedTelemetryExport | None = None,
    trace_platform: bool = False,
    tcp_socket: socket.socket | None = None,
    monotonic_clock: IMonotonicClock | None = None,
    extend: Callable[[LegacyRegistry], None] | None = None,
    extra_values: ScopeValues | None = None,
) -> HostProcess:
    """The host graph from its file, with no runner yet, its records reconciled; ``halted`` it keeps to report.
    ``trace_platform`` builds the tracing handles config and ``environ`` decide, else the given ones are used.
    ``tcp_socket`` is a listener already bound at the port; ``monotonic_clock`` defaults to the system's. Disposes
    partial resources on failure."""
    graph = _build_host_graph(
        host.host_view(),
        subscriptions=host.subscriptions,
        events=events,
        environ=environ,
        clock=resolve_clock(os.environ if environ is None else environ),
        monotonic_clock=monotonic_clock or SystemMonotonicClock(),
        platform_tracing=platform_tracing,
        received_telemetry=received_telemetry,
        trace_platform=trace_platform,
        host_token=host.host_token.value,
        tcp_socket=tcp_socket,
        extend=extend,
        extra_values=extra_values,
    )
    try:
        graph.host_settings = HostSettingsInForce(
            HostSettingsReader(host, graph.host_stores.settings, host_record_reads(graph.host_stores)),
            tool_profile=graph.tool_profile,
            tracing=TracingSettings.of(os.environ if environ is None else environ),
            clock=graph.clock,
        )
        graph.host_settings_authoring = HostSettingsAuthoring(
            graph.host_settings.reader, graph.host_stores.settings, HOST_REGISTRY, drivers=graph.wakes
        )
        graph.host_settings_application = build_host_settings_application(graph, graph.host_settings.reader)
        if graph.host_settings.reader.records is not None:
            graph.records_reconcile = HostRecordsReconcile(
                host.root,
                workspaces=graph.workspaces,
                children=graph.children,
                tool_profile=graph.tool_profile,
                stores=graph.host_stores,
                clock=graph.clock,
                usage_http_client=graph.usage_http_client,
                tick_in_force=graph.tick_in_force,
                runners=graph.runner_roster.current,
                registration=graph.host_registration,
                retired_workspaces=graph.host_settings.reader.retired_workspaces,
            )
        if graph.readiness.evaluate().ready:
            effective = graph.host_settings.take()
            # Before any runner resumes: a retired workspace's provider releases what it holds.
            if graph.records_reconcile is not None and graph.host_settings.taken is not None:
                graph.records_reconcile.run(effective, graph.host_settings.taken)
        # Host entries read on use: each worker export is admitted under the host record's current revision.
        graph.infrastructure.resolver.get(ReceiverTracingInForce).hold(
            graph.host_settings.reader.live(_receiver_tracing)
        )
        graph.halted = tuple(halted)
        graph.readiness = HostReadiness(
            reader=graph.readiness.reader,
            expected_revision=graph.readiness.expected_revision,
            halted=graph.halted_runners,
        )
        return graph
    except BaseException as failure:
        try:
            graph.close()
        except BaseException as cleanup:
            failure.add_note(f"closing the host after initialization also failed: {cleanup!r}")
        raise


def build_host_of_one(
    config: RunnerConfig,
    *,
    clock: IClock,
    events: EventBroker | None = None,
    environ: Mapping[str, str] | None = None,
    platform_tracing: IPlatformTracing | None = None,
    received_telemetry: IReceivedTelemetryExport | None = None,
    host_token: str = "",
    monotonic_clock: IMonotonicClock | None = None,
    extend: Callable[[LegacyRegistry], None] | None = None,
    extra_values: ScopeValues | None = None,
) -> HostProcess:
    """The host a standalone command builds for one runner, from that runner's ``config``, with no runner on it yet;
    ``host_token`` is the host's own credential. The other arguments are :func:`build_host_process`'s."""
    return _build_host_graph(
        config,
        subscriptions=config.subscriptions,
        events=events,
        environ=environ,
        clock=clock,
        monotonic_clock=monotonic_clock or SystemMonotonicClock(),
        platform_tracing=platform_tracing,
        received_telemetry=received_telemetry,
        trace_platform=False,
        host_token=host_token,
        extend=extend,
        extra_values=extra_values,
    )


def compose_runner(
    host_config: HostConfig,
    host: HostProcess,
    built: BuiltRunner,
    *,
    bundle: BundleSnapshot | None = None,
    selection: BindingSelection | None = None,
    publish_bundle: bool = False,
    environ: Mapping[str, str] | None = None,
    trace_exporter: ITraceExporter | None = None,
    runner_trace_export: RunnerTraceExport | None = None,
    settings: bool = True,
) -> RunnerProcess:
    """One runner composed over ``host`` and bound to ``built``'s id — its graph, its harness set, and, with
    ``settings``, its settings reader over the entry it projected from — not yet placed among the host's runners."""
    runner = _build_runner(
        built.config,
        host,
        bundle=bundle,
        selection=selection,
        publish_bundle=publish_bundle,
        environ=environ,
        trace_exporter=trace_exporter,
        runner_trace_export=runner_trace_export,
        runner_id=built.id,
        token=built.token,
    )
    if not settings:
        return runner
    entry = built.entry or next((entry for entry in host_config.runners if entry.id == built.id), None)
    if entry is None:
        return runner
    reader = RunnerSettingsReader(
        host_config,
        entry,
        runner.stores.settings,
        host_settings=host.host_settings.reader if host.host_settings is not None else None,
    )
    seed = reader.seeder(runner.stores.settings) if entry.authored is not None else None
    return replace(runner, settings_reader=reader, settings_seed=seed)


class IRunnerResume(Protocol):
    """One runner's restart-resume hooks, over its own store."""

    def on_startup(self) -> int: ...
    def recover_takeovers(self) -> int: ...
    def reconcile_sessions(self) -> int: ...
    def on_shutdown(self) -> int: ...


class IWiredRunner(Protocol):
    """One runner's served routes and stepper, as the app wired them."""

    def quiesce(self) -> None:
        """Wait out the runner's in-flight pass."""
        ...

    def close(self) -> None:
        """Close its stepper and the hub clients its routes own."""
        ...


class IRunnerDrivers(Protocol):
    """One runner's timed drivers — none on a ``stepped`` host."""

    def start(self) -> None: ...
    def stop(self) -> None: ...


class NoDrivers:
    """The drivers of a runner on a ``stepped`` host, which passes only on request."""

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


@domain_model
@dataclass(frozen=True)
class StartupNote:
    """What one of a runner's startup steps did: its event, the runner, and how many it touched — the operator's
    sentence in :attr:`message`, the fields a log consumer filters on in the rest."""

    event: str
    runner_id: str
    count: int
    message: str


class IRunnerStart[W: IWiredRunner, R: IRunnerResume](Protocol):
    """The seams starting a runner reaches beyond composition: the app's wiring and registry, and the loop's resume
    hooks, scratch reconcile and drivers — injected, since composition imports neither."""

    def resume(self, runner: RunnerProcess) -> R:
        """``runner``'s restart-resume hooks."""
        ...

    def reconcile_scratch(self, runner: RunnerProcess) -> None:
        """Reconcile ``runner``'s worker scratch before it can spawn."""
        ...

    def announce(self, note: StartupNote) -> None:
        """Tell the operator what a startup step did."""
        ...

    def wire(self, runner: RunnerProcess) -> W:
        """Wire ``runner``'s routes and stepper, not yet served."""
        ...

    def serve(self, runner: RunnerProcess, wired: W) -> None:
        """Serve ``wired`` in the app's registry."""
        ...

    def withdraw(self, runner_id: str) -> None:
        """Stop serving ``runner_id``, which then answers ``404``."""
        ...

    def halt(self, runner: HaltedRunner) -> None:
        """Hold ``runner`` halted in the app's registry, its routes answering ``503`` with its reasons."""
        ...

    def drive(self, runner: RunnerProcess, wired: W) -> IRunnerDrivers:
        """Start ``runner``'s drivers; none on a ``stepped`` host."""
        ...


@dataclass
class LiveRunner[W: IWiredRunner, R: IRunnerResume]:
    """One runner the host runs: its graph and resume hooks, then its wiring and drivers once it is served."""

    runner: RunnerProcess
    resume: R
    wired: W | None = None
    drivers: IRunnerDrivers | None = None
    #: Its child scope; ``None`` under a set bound to no parent scope.
    child: OwnedChild | None = None
    #: Whether its startup steps ran, so its shutdown marks its in-flight leases.
    started_up: bool = False

    @property
    def runner_id(self) -> str:
        return self.runner.runner_id


class HostRunnerSet[W: IWiredRunner, R: IRunnerResume]:
    """The host's live runners. Every change to ``host.runners``, the app's registry and each runner's drivers and
    clients goes through here, under one lock, so the host tick and a request never see a runner half-placed.

    It alone opens and closes runner children. Bound to no parent scope, it opens none."""

    def __init__(self, host: HostProcess) -> None:
        self.host = host
        self._lock = threading.RLock()
        self._live: dict[str, LiveRunner[W, R]] = {}
        self._parent: OwnedChild | None = None
        self._eager: tuple[type[Any], ...] = ()
        self._child_values = ScopeValues()
        self._closed = False
        self._closed_graphs: dict[int, RunnerProcess] = {}

    def bind(
        self, parent: OwnedChild, *, eager: Sequence[type[Any]] = (), child_values: ScopeValues | None = None
    ) -> None:
        """Open runner children beneath the borrowed ``parent`` from now on, building ``eager`` as each opens."""
        self._parent = parent
        self._eager = tuple(eager)
        self._child_values = child_values or ScopeValues()

    def open_runner(self, built: BuiltRunner) -> OwnedChild | None:
        """A new child scope for ``built``, owned by its runner id, with the eager types built; ``None`` when unbound.

        A child whose eager types fail is closed before the failure is raised. Refused after :meth:`close_all`."""
        with self._lock:
            if self._closed:
                raise RuntimeError(f"runner {built.id}: the host's runners are closed")
        if self._parent is None:
            return None
        child = open_child(
            self._parent,
            lifetime=Lifetime.RUNNER,
            owner=built.id,
            values=ScopeValues((supply(port(BuiltRunner), built), *self._child_values.items)),
        )
        try:
            for dependency_type in self._eager:
                child.resolver.get(dependency_type)
        except BaseException as failure:
            close_after(functools.partial(child.close, failure), failure)
            raise
        return child

    def open_composed(self, built: BuiltRunner) -> tuple[RunnerProcess, OwnedChild]:
        """Open ``built``'s child and read the runner it composes; a failure closes the child. Refused unbound."""
        child = self.open_runner(built)
        if child is None:
            raise RuntimeError(f"runner {built.id}: the host's runners open no child")
        try:
            return child.resolver.get(RunnerProcess), child
        except BaseException as failure:
            close_after(functools.partial(self.close_graph, None, child), failure)
            raise

    def close_graph(self, runner: RunnerProcess | None, child: OwnedChild | None) -> None:
        """Close one runner's graph, in reverse construction order: its process graph, then its child. ``runner`` is
        ``None`` for a child opened for a runner that was never composed."""
        if runner is not None:
            with self._lock:
                if id(runner) in self._closed_graphs:
                    return
                self._closed_graphs[id(runner)] = runner
        try:
            if runner is not None:
                runner.close_runner()
        except BaseException as failure:
            if child is not None:
                try:
                    child.close()
                except BaseException as cleanup:
                    failure.add_note(f"closing the runner scope after it also failed: {cleanup!r}")
            raise
        else:
            if child is not None:
                child.close()

    def live(self) -> tuple[LiveRunner[W, R], ...]:
        """Every live runner, in the host's runner order."""
        with self._lock:
            return tuple(self._live[runner.runner_id] for runner in self.host.runners if runner.runner_id in self._live)

    def get(self, runner_id: str) -> LiveRunner[W, R] | None:
        with self._lock:
            return self._live.get(runner_id)

    def running(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._live)

    def place(self, live: LiveRunner[W, R]) -> None:
        """Hold ``live`` live, its runner among the host's runners. Refused after :meth:`close_all`, which would
        never close it: its caller still owns it."""
        runner = live.runner
        with self._lock:
            if self._closed:
                raise RuntimeError(f"runner {runner.runner_id}: the host's runners are closed")
            self._live[runner.runner_id] = live
            if all(held.runner_id != runner.runner_id for held in self.host.runners):
                self.host.runners = (*self.host.runners, runner)
            self.host.halted = tuple(halted for halted in self.host.halted if halted.id != runner.runner_id)

    def remove(self, runner_id: str) -> LiveRunner[W, R] | None:
        """Drop ``runner_id`` from the host's runners; the live runner it was, if any."""
        with self._lock:
            live = self._live.pop(runner_id, None)
            self.host.runners = tuple(runner for runner in self.host.runners if runner.runner_id != runner_id)
            return live

    def serve(self, live: LiveRunner[W, R], start: IRunnerStart[W, R]) -> None:
        """Wire ``live``'s routes and stepper, then serve it and start its drivers as one change."""
        wired = start.wire(live.runner)
        with self._lock:
            live.wired = wired
            start.serve(live.runner, wired)
            live.drivers = start.drive(live.runner, wired)

    def abandon(self, runner_id: str, start: IRunnerStart[W, R]) -> None:
        """Undo a start that failed after its runner was placed: withdraw it, stop and close what it got, and drop it
        from the host's runners. Its graph closes before its wiring. Its sealed token stays — it is not retired."""
        start.withdraw(runner_id)
        live = self.remove(runner_id)
        if live is None:
            return
        # Unwound last-registered first: the drivers, then the graph, then the wiring.
        with contextlib.ExitStack() as closing:
            if live.wired is not None:
                closing.callback(live.wired.close)
            closing.callback(self.close_graph, live.runner, live.child)
            if live.drivers is not None:
                closing.callback(live.drivers.stop)

    def halt(self, halted: HaltedRunner) -> None:
        """Hold ``halted`` among the host's halted runners, in place of any of its id."""
        with self._lock:
            self.host.halted = (*(held for held in self.host.halted if held.id != halted.id), halted)

    def unhalt(self, runner_id: str) -> None:
        """Stop holding ``runner_id`` among the host's halted runners."""
        with self._lock:
            self.host.halted = tuple(held for held in self.host.halted if held.id != runner_id)

    def stop_all(self, *, mark: bool) -> list[str]:
        """Stop every live runner's drivers, wait out its in-flight pass, then — with ``mark`` — mark its in-flight
        leases for restart-resume and drain their workers; the shutdown's messages. Its graph and wiring stay for
        :meth:`close_all`."""
        said: list[str] = []
        live = self.live()
        for runner in reversed(live):
            if runner.drivers is not None:
                runner.drivers.stop()
        for runner in live:
            if runner.wired is not None:
                runner.wired.quiesce()
        if mark:
            for runner in live:
                if runner.started_up and (marked := runner.resume.on_shutdown()):
                    said.append(f"marked {marked} in-flight lease(s) for restart-resume")
        return said

    def close_all(self) -> None:
        """Close every live runner's graph, then its wiring, attempting each, and refuse further opens. Later calls do
        nothing."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        failure: BaseException | None = None
        for live in reversed(self.live()):
            closers: list[Callable[[], None]] = [functools.partial(self.close_graph, live.runner, live.child)]
            if live.wired is not None:
                closers.append(live.wired.close)
            for close in closers:
                try:
                    close()
                except BaseException as cleanup:
                    if failure is None:
                        failure = cleanup
                    else:
                        failure.add_note(f"closing another runner resource also failed: {cleanup!r}")
        if failure is not None:
            raise failure


def close_after(close: Callable[[], None], failure: BaseException) -> None:
    """Close what ``failure`` abandoned; a cleanup error is noted on it rather than raised over it."""
    try:
        close()
    except Exception as cleanup:
        failure.add_note(f"closing the runner after it also failed: {cleanup!r}")


class RunnerStartFailed(Exception):
    """A runner's start failed at ``step``; nothing of it is served."""

    def __init__(self, runner_id: str, step: RunnerStartStep, error: str) -> None:
        super().__init__(f"runner {runner_id}: {step}: {error}")
        self.runner_id = runner_id
        self.step: RunnerStartStep = step
        self.error = error


@contextlib.contextmanager
def _start_step(runner_id: str, step: RunnerStartStep) -> Generator[None, None, None]:
    try:
        yield
    except RunnerStartFailed:
        raise
    except Exception as exc:
        raise RunnerStartFailed(runner_id, step, str(exc) or type(exc).__name__) from exc


def run_startup_steps[W: IWiredRunner, R: IRunnerResume](live: LiveRunner[W, R], start: IRunnerStart[W, R]) -> None:
    """``live``'s per-runner startup steps, in the boot's order: the worker-shell refusal, the rejected-tracing
    announcement, its worker-scratch reconcile, then crash-orphan marking, takeover recovery and session reconcile —
    all before any of its passes or requests can begin. Raises :class:`RunnerStartFailed` naming the step."""
    runner, runner_id = live.runner, live.runner_id
    with _start_step(runner_id, "worker_shell"):
        if refusal := worker_shell_refusal(runner.config.worker_shell):
            raise ConfigError(f"runner {runner.config.name}: {refusal}")
    with _start_step(runner_id, "tracing"):
        announce_rejected_tracing(runner.trace_settings, runner.stores.outbound, runner.clock.now())
    with _start_step(runner_id, "worker_scratch"):
        start.reconcile_scratch(runner)
    with _start_step(runner_id, "resume"):
        if resumable := live.resume.on_startup():
            start.announce(
                StartupNote(
                    "marked crash-interrupted leases for restart-resume",
                    runner_id,
                    resumable,
                    f"marked {resumable} crash-interrupted lease(s) for restart-resume",
                )
            )
    live.started_up = True
    # After the marking, which skips a held lease; before any thread or request can begin an entry.
    with _start_step(runner_id, "takeovers"):
        if interrupted := live.resume.recover_takeovers():
            start.announce(
                StartupNote(
                    "ended crash-interrupted takeovers",
                    runner_id,
                    interrupted,
                    f"ended {interrupted} takeover(s) a crash interrupted mid-entry",
                )
            )
    # After the takeovers settle, and before any thread can dispatch a restart resume.
    with _start_step(runner_id, "sessions"):
        if stopped := live.resume.reconcile_sessions():
            start.announce(
                StartupNote(
                    "stopped turns a crash left running without a worker",
                    runner_id,
                    stopped,
                    f"stopped {stopped} turn(s) a crash left running without a worker",
                )
            )


def serve_runner[W: IWiredRunner, R: IRunnerResume](
    live: LiveRunner[W, R], runners: HostRunnerSet[W, R], start: IRunnerStart[W, R]
) -> None:
    """Serve a started ``live`` runner: wire its routes and stepper, register it, and start its drivers."""
    with _start_step(live.runner_id, "serve"):
        runners.serve(live, start)


@collaborator
@dataclass(frozen=True)
class StartingRunner[W: IWiredRunner, R: IRunnerResume]:
    """A runner composed, its startup steps run and placed among the host's runners, not yet served: the caller
    reports the host once between preparing a batch and running it, so a runner the hub released is told so before its
    first tick."""

    live: LiveRunner[W, R]
    runners: HostRunnerSet[W, R]
    start: IRunnerStart[W, R]

    @property
    def runner_id(self) -> str:
        return self.live.runner_id

    def run(self) -> None:
        """Serve the runner and start its drivers. Raises :class:`RunnerStartFailed`."""
        serve_runner(self.live, self.runners, self.start)


def start_runner[W: IWiredRunner, R: IRunnerResume](
    built: BuiltRunner, start: IRunnerStart[W, R], runners: HostRunnerSet[W, R]
) -> StartingRunner[W, R]:
    """Open ``built``'s child, which composes its runner, run its startup steps, and place it among the host's
    runners. Raises :class:`RunnerStartFailed` naming the step, with nothing of the runner left behind."""
    with _start_step(built.id, "compose"):
        runner, child = runners.open_composed(built)
    try:
        live = LiveRunner[W, R](runner, start.resume(runner), child=child)
        run_startup_steps(live, start)
        with _start_step(built.id, "serve"):
            runners.place(live)
    except BaseException as failure:
        close_after(functools.partial(runners.close_graph, runner, child), failure)
        raise
    return StartingRunner(live, runners, start)


def drain_runner[W: IWiredRunner, R: IRunnerResume](
    host: HostProcess, runners: HostRunnerSet[W, R], start: IRunnerStart[W, R], runner_id: str
) -> bool:
    """Remove one retired runner, touching no other: withdraw its routes, stop its drivers, wait out its in-flight
    pass, mark and drain its workers, close its graph then its wiring, drop it from the host's runners, then delete its
    sealed token. Its identity and settings rows stay. Whether it was running."""
    live = runners.get(runner_id)
    if live is None:
        return False
    start.withdraw(runner_id)
    if live.drivers is not None:
        live.drivers.stop()
    if live.wired is not None:
        live.wired.quiesce()
    with contextlib.ExitStack() as draining:
        # Unwound last-registered first: the shutdown marking, then the graph, the wiring, the removal and the token.
        draining.callback(host.host_stores.hub_tokens.drop, runner_id)
        draining.callback(runners.remove, runner_id)
        if live.wired is not None:
            draining.callback(live.wired.close)
        draining.callback(runners.close_graph, live.runner, live.child)
        if live.started_up:
            live.resume.on_shutdown()
    get_logger("blizzard.runner.host").info("drained a retired runner", runner_id=runner_id)
    return True


class IHostedRunnerSource(Protocol):
    """What starts a runner the store hosts: its seeded projection, read at the tick that starts it."""

    def built(self, runner_id: str) -> BuiltRunner | HaltedRunner: ...


@dataclass
class HostRunnerReconcile[W: IWiredRunner, R: IRunnerResume]:
    """The host tick's reconcile of its live runners against the store's hosted ones: it starts each active runner not
    running — retrying a failed start — and drains each retired runner still running; a retired runner's leftover
    sealed token is deleted. A runner that fails to start is held in ``failures`` and left unstarted, and its
    neighbours keep ticking."""

    host: HostProcess
    runners: HostRunnerSet[W, R]
    start: IRunnerStart[W, R]
    source: IHostedRunnerSource
    failures: RunnerStartFailures = field(default_factory=RunnerStartFailures)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def run(self) -> None:
        with self._lock:
            roster = self.host.host_stores.hosted_runners.roster()
            retired = roster.retired
            plan = reconcile(roster.active, retired, self.runners.running(), self.failures.failed())
            for runner_id in plan.forget_failures:
                self.failures.clear(runner_id)
            for runner_id in plan.drain:
                drain_runner(self.host, self.runners, self.start, runner_id)
            # A retired runner not running — retired while the host was stopped, or halted — is withdrawn and its
            # leftover token dropped; only one still holding a token costs the tick a write.
            stale = retired - self.runners.running()
            for runner_id in sorted(stale & self.host.host_stores.hub_tokens.held_runner_ids()):
                self.start.withdraw(runner_id)
                self.host.host_stores.hub_tokens.drop(runner_id)
            for runner_id in sorted(stale & {runner.id for runner in self.host.halted if runner.id is not None}):
                self.start.withdraw(runner_id)
                self.runners.unhalt(runner_id)
            starting = [prepared for runner_id in plan.start if (prepared := self._prepare(runner_id)) is not None]
            if not starting:
                return
            # One report between preparing the batch and running it: a runner the hub released claims nothing.
            self.host.tick_in_force.current().report()
            for prepared in starting:
                try:
                    prepared.run()
                except RunnerStartFailed as exc:
                    try:
                        self.runners.abandon(prepared.runner_id, self.start)
                    except Exception:  # the start failure is the one held; its siblings still start
                        get_logger("blizzard.runner.host").exception(
                            "closing a runner that failed to start also failed", runner_id=prepared.runner_id
                        )
                    self._failed(prepared.runner_id, exc)
                else:
                    self.failures.clear(prepared.runner_id)
                    get_logger("blizzard.runner.host").info("started a hosted runner", runner_id=prepared.runner_id)

    def _prepare(self, runner_id: str) -> StartingRunner[W, R] | None:
        try:
            with _start_step(runner_id, "seed"):
                built = self.source.built(runner_id)
                if isinstance(built, HaltedRunner):
                    raise RunnerStartFailed(runner_id, "seed", "; ".join(built.errors))
            return start_runner(built, self.start, self.runners)
        except RunnerStartFailed as exc:
            self._failed(runner_id, exc)
            return None

    def _failed(self, runner_id: str, exc: RunnerStartFailed) -> None:
        failure = RunnerStartFailure(exc.step, exc.error)
        if self.failures.hold(runner_id, failure):
            get_logger("blizzard.runner.host").error(
                "a hosted runner failed to start", runner_id=runner_id, step=exc.step, detail=exc.error
            )
        name = next((runner.name for runner in self.host.halted if runner.id == runner_id), runner_id)
        halted = HaltedRunner(name, runner_id, (f"failed to start at {exc.step}: {exc.error}",))
        self.runners.halt(halted)
        self.start.halt(halted)


def _build_host_graph(
    config: RunnerConfig,
    *,
    subscriptions: Sequence[SubscriptionDeclaration],
    events: EventBroker | None,
    environ: Mapping[str, str] | None,
    clock: IClock,
    monotonic_clock: IMonotonicClock,
    platform_tracing: IPlatformTracing | None,
    received_telemetry: IReceivedTelemetryExport | None,
    trace_platform: bool,
    host_token: str = "",
    tcp_socket: socket.socket | None = None,
    extend: Callable[[LegacyRegistry], None] | None = None,
    extra_values: ScopeValues | None = None,
) -> HostProcess:
    """The host's own graph, with no runner on it yet. ``config`` carries the host's keys — listeners, tracing, the
    workspaces, its hub — and ``subscriptions`` are the host's declarations, which its credential renewal covers.
    ``host_token`` is the host's own credential; a host holding one is registered, and reports itself to its hub.
    Its flags are the resolution ``config``'s load made, or — for a config built in code — the registry it carries
    at its defaults under its overrides; each override it ignores is warned of here, once."""
    install_runner_guard()
    from blizzard.runner.host_root import open_host_infrastructure
    from blizzard.runner.hub.host_options import HostHubOptions
    from blizzard.runner.lifecycle.host_lane_drain import HostLaneDrain
    from blizzard.runner.tracing.host_options import HostTracingOptions
    from blizzard.runner.wiring import HostRuntimeOptions

    env = os.environ if environ is None else environ
    roster = HostRunnerRoster()
    tick_in_force = HostTickInForce(HostTick(), tuple(subscriptions))
    held_workspaces: list[HostWorkspaces] = []
    owner = open_host_infrastructure(
        config,
        clock,
        monotonic_clock,
        HostRuntimeOptions(env, events, tcp_socket),
        HostHubOptions(
            config.hub_url,
            host_token,
            lambda: host_report(config, held_workspaces[0], tick_in_force.subscriptions()),
            roster.current,
        ),
        HostTracingOptions(
            config.tracing, env, declared_telemetry_names(), platform_tracing, received_telemetry, trace_platform
        ),
        tick_in_force=tick_in_force,
        extend=extend,
        extra_values=extra_values,
    )
    try:
        resolver = owner.resolver
        engine = resolver.get(Engine)
        platform_tracing = resolver.get(IPlatformTracing)
        received_telemetry = resolver.get(IReceivedTelemetryExport)
        readiness = resolver.get(ReadinessService)
        host_stores = resolver.get(HostStores)
        usage_http_client = resolver.get(LazyUsageHttpClient)
        children = resolver.get(IChildProcesses)
        tool_profile = resolver.get(ToolProfileHolder)
        workspaces = HostWorkspaces(
            build_workspaces(
                config.workspaces,
                config.root,
                launch=WorkspaceLaunch(children=children, env=tool_profile),
                audit=host_stores.runner_audit,
            )
        )
        held_workspaces.append(workspaces)
        host_identity = resolver.get(HostIdentityHolder)
        host_registration = resolver.get(HostRegistrationHolder)
        host_hub = resolver.get(HttpHostHubClient | None)
        process = resolver.get(LinuxProcessProbe)
        host = HostProcess(
            engine=engine,
            readiness=readiness,
            host_stores=host_stores,
            listeners=resolver.get(Listeners),
            workspaces=workspaces,
            events=resolver.get(EventBroker | None),
            clock=clock,
            monotonic_clock=monotonic_clock,
            zone=resolver.get(tzinfo),
            process=process,
            children=children,
            invariants=resolver.get(HostInvariantCheck),
            hub_tokens=build_hub_tokens(
                host_stores.hub_tokens, config.root, environ=os.environ if environ is None else environ, clock=clock
            ),
            telemetry_receiver=resolver.get(TelemetryReceiver),
            tick_in_force=tick_in_force,
            runner_roster=roster,
            usage_http_client=usage_http_client,
            flag_registry=resolver.get(FlagRegistry),
            flags=resolver.get(Flags),
            platform_tracing=platform_tracing,
            received_telemetry=received_telemetry,
            host_identity=host_identity,
            host_registration=host_registration,
            host_hub=host_hub,
            tool_profile=tool_profile,
            retirements=resolver.get(RetirementNotices),
            infrastructure=owner,
        )
        tick_in_force.hold(
            HostTick(
                registration=resolver.get(HostRegistration | None),
                external_usage_sample=_external_usage_sample_pass(
                    subscriptions, host_stores, clock, usage_http_client, roster.current, registration=host_registration
                ),
                lane_drain=resolver.get(HostLaneDrain | None),
                credential_renewal=credential_renewal_pass(
                    subscriptions, host_stores, clock, children=children, env=tool_profile
                ),
            ),
            tuple(subscriptions),
        )
        return host
    except BaseException as failure:
        try:
            owner.close()
        except BaseException as cleanup:
            failure.add_note(f"closing the host scope after initialization also failed: {cleanup!r}")
        raise


#: How long one of the host's own hub calls waits — its report bounds the start-up wait before the runners tick.
_HOST_HUB_TIMEOUT_SECONDS = 10.0


def host_report(
    config: RunnerConfig, workspaces: IWorkspaceEntries, subscriptions: Sequence[SubscriptionDeclaration]
) -> HostReport:
    """What the host reports about itself: every public URL in declaration order, the panel's sign-in callback URIs,
    each workspace it serves with the environments it holds, and the subscriptions it serves — a retired one of
    either never among them."""
    reported = [
        HostReportedWorkspace(workspace_id=entry.workspace_id, env_capacity=entry.provider.capacity())
        for entry in workspaces.entries()
    ]
    return HostReport(
        url=config.public_url or None,
        redirect_uris=config.redirect_uris,
        urls=config.public_origins.urls,
        workspaces=tuple(reported),
        subscriptions=tuple(
            HubSubscriptionDeclaration(slug=declared.slug, name=declared.name, provider=declared.provider)
            for declared in subscriptions
        ),
    )


def _identity_remedy(config: RunnerConfig, runner_id: str) -> str:
    """What restores a runner whose token resolves to another runner than the one configured, or is refused."""
    return (
        f"set the token of runner {runner_id} with `blizzard runner token set --runner {config.name}`, "
        f"or correct `[[runner]] id` in {HostLayout(config.root).config_path}"
    )


def harness_assembly_key(inputs: HarnessAssemblyInputs) -> HarnessAssemblyKey:
    """Observe the external identities used by a prospective harness assembly."""
    identities = ToolBinaryIdentity(inputs.worker_env.tool())
    return HarnessAssemblyKey(
        values=(inputs,),
        binary_identities=tuple(
            (section.harness_id, identities.identify(section.binary)) for section in inputs.sections
        ),
        bundle_source_identity=(
            bundle_source_identity(
                inputs.config_dir,
                bundle_composition_inputs(HarnessSections(inputs.sections), autonomy=inputs.autonomy, root=inputs.root),
            )
            if inputs.publish and inputs.config_dir is not None
            else inputs.bundle
        ),
    )


def _build_runner(
    config: RunnerConfig,
    host: HostProcess,
    *,
    bundle: BundleSnapshot | None,
    selection: BindingSelection | None,
    publish_bundle: bool,
    environ: Mapping[str, str] | None,
    trace_exporter: ITraceExporter | None,
    runner_trace_export: RunnerTraceExport | None,
    runner_id: str,
    token: StoredToken | None,
) -> RunnerProcess:
    """Compose one runner and its harness-set builder over ``host``'s engine.

    Stores bind to ``runner_id`` before use. A supplied ``selection`` overrides selection in the builder;
    ``bzh:pluggable-seams`` owns binding lifetime."""
    identity = RunnerIdentityHolder()
    platform_tracing = host.platform_tracing
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="blizzard-spawner")
    identity_client: httpx2.Client | None = None
    trace = runner_trace_export
    try:
        # Seeded only from a store at the code's schema head: a store mid-migration fails readiness, not boot.
        ready = host.readiness.evaluate().ready
        connections = RunnerStoreConnections(host.engine, RunnerStoreErrorFactory(get_logger("blizzard.runner.store")))
        connections.bind(runner_id)
        stores = _build_stores(connections, host.clock)
        clock = host.clock
        registered = stores.identity.runner_identity() if ready else None
        if registered is not None:
            identity.hold(registered)
        runner_environ = os.environ if environ is None else environ
        tracing = TracingSettings.of(runner_environ)
        harness_settings = replace(config.harness_settings(tracing), runner_id=runner_id)
        assembly_inputs = HarnessAssemblyInputs(
            root=harness_settings.root,
            autonomy=harness_settings.autonomy,
            config_dir=harness_settings.config_dir,
            worker_env=harness_settings.worker_env,
            transcripts_root=harness_settings.transcripts_root,
            sections=harness_settings.sections.sections,
            runner_id=runner_id,
            telemetry_enabled=config.tracing.harness_telemetry and platform_tracing.enabled,
            runner_environ=tuple(sorted(runner_environ.items())),
            spawn_roots=bound_spawn_roots(config, host.workspaces),
            publish=publish_bundle,
            bundle=bundle,
        )
        version_env = assembly_inputs.worker_env.tool()
        binary_versions = BinaryVersionCache(ToolBinaryIdentity(version_env))

        def assemble(key: HarnessAssemblyKey, *, recovering: bool = False) -> HarnessSet:
            nonlocal version_env, binary_versions
            inputs = cast(HarnessAssemblyInputs, key.values[0])
            sections = HarnessSections(inputs.sections)
            settings = HarnessSettings(
                inputs.root,
                inputs.autonomy,
                inputs.config_dir,
                inputs.worker_env,
                inputs.transcripts_root,
                sections,
                inputs.runner_id,
            )
            tool_env = inputs.worker_env.tool()
            if tool_env != version_env:
                version_env = tool_env
                binary_versions = binary_versions.with_identities(ToolBinaryIdentity(tool_env))
            versions = CachedVersionProbe(VersionProbe(host.children, tool_env), binary_versions)
            held_selection = harness_sets.current.selection if key.preserve_selection and not recovering else None
            chosen = (
                held_selection
                or (None if recovering else selection)
                or select_harness_bindings(sections, versions=versions)
            )
            recovery_sections: tuple[Any, ...] = ()
            pending_recovery: set[str] = set()
            if not recovering and ready:
                active_ids = {
                    lease.harness_id
                    for lease in stores.lease_record.list_active_leases()
                    if lease.session_id is not None and lease.harness_id is not None
                }
                recovery_ids: set[str] = set()
                families = harness_families()
                for section in sections:
                    if section.enabled or section.harness_id not in active_ids:
                        continue
                    family = families[section.harness_id]
                    binary = probed_binary(family, section)
                    if not versions.binary_present(binary):
                        continue
                    if len(family) > 1:
                        raw = versions.observe_version(binary)
                        if not any(cast(IVersionSelectedDeclaration[Any], item).admits_version(raw) for item in family):
                            pending_recovery.add(section.harness_id)
                            binary_versions.forget(binary)
                            continue
                    recovery_ids.add(section.harness_id)
                if recovery_ids:
                    recovery_sections = tuple(
                        replace(cast(Any, section), enabled=True) if section.harness_id in recovery_ids else section
                        for section in sections
                    )
                    recovery_selection = select_harness_bindings(
                        HarnessSections(recovery_sections),
                        versions=versions,
                    )
                    chosen = BindingSelection(
                        {
                            harness_id: recovery_selection.of(harness_id) if harness_id in recovery_ids else declaration
                            for harness_id, declaration in chosen.chosen.items()
                        }
                    )
            if key.retry_unresolved and frozenset(pending_recovery) == harness_sets.current.pending_recovery:
                return harness_sets.current
            snapshot = inputs.bundle
            if inputs.publish and inputs.config_dir is not None and snapshot is None:
                snapshot = publish_harness_bundle(
                    inputs.config_dir,
                    inputs.root,
                    autonomy=inputs.autonomy,
                    sections=sections,
                    selection=chosen,
                )
            environment = dict(inputs.runner_environ)
            plan = combined_telemetry_plan(
                settings,
                selection=chosen,
                bundle=snapshot,
                harness_telemetry_enabled=inputs.telemetry_enabled,
                runner_environ=environment,
            )
            corpus = CommittedCorpus()
            harnesses, probes = build_production_harnesses(
                settings,
                selection=chosen,
                executor=executor,
                process=host.process,
                children=host.children,
                clock=clock,
                monotonic_clock=host.monotonic_clock,
                spawn_roots=inputs.spawn_roots,
                corpus=corpus,
                bundle=snapshot,
                harness_telemetry_enabled=inputs.telemetry_enabled,
                runner_environ=environment,
                versions=versions,
            )
            default_id = default_harness_id(harnesses)
            if default_id is not None:
                harnesses.transcript_source(default_id)
            health = HarnessHealthCache(
                clock=clock,
                probes=probes,
                corpus=corpus,
                selftest_results=stores.selftest_results,
                configured_tiers=configured_tiers(sections),
            )
            capability_versions = HarnessVersionCache(
                clock=clock,
                binary_versions=binary_versions,
                binaries={section.harness_id: section.binary for section in sections if section.enabled},
                service_harnesses=frozenset(
                    section.harness_id
                    for section in sections
                    if section.enabled and chosen.of(section.harness_id).service_version
                ),
            )
            selected_set = HarnessSet(
                chosen,
                snapshot,
                harnesses,
                probes,
                health,
                plan,
                capability_versions,
                recovery_only=recovering,
                pending_recovery=frozenset(pending_recovery),
            )
            if recovery_sections:
                recovery_key = replace(
                    key, values=(replace(inputs, sections=recovery_sections, publish=False, bundle=snapshot),)
                )
                selected_set = replace(selected_set, recovery=assemble(recovery_key, recovering=True))
            return selected_set

        harness_sets = HarnessSets(assemble)
        harness_sets.select(harness_assembly_key(assembly_inputs))

        def key_for(current: RunnerConfig) -> HarnessAssemblyKey:
            settings = replace(current.harness_settings(tracing), runner_id=runner_id)
            return harness_assembly_key(
                replace(
                    assembly_inputs,
                    root=settings.root,
                    autonomy=settings.autonomy,
                    config_dir=settings.config_dir,
                    worker_env=settings.worker_env,
                    transcripts_root=settings.transcripts_root,
                    sections=settings.sections.sections,
                    telemetry_enabled=current.tracing.harness_telemetry and platform_tracing.enabled,
                    spawn_roots=bound_spawn_roots(current, host.workspaces),
                )
            )

        if trace is None:
            trace = host.infrastructure.resolver.get(RunnerTraceExportFactory).build(
                RunnerTraceOptions(runner_environ, trace_exporter)
            )
        exporter = trace.exporter
        trace_sweep = (
            LeaseTraceSweep(
                leases=stores.lease_traces,
                outbound=stores.outbound,
                exporter=exporter,
                identity=identity,
                clock=clock,
            )
            if exporter is not None
            else None
        )

        def replay_tracing() -> TracingConfig:
            # Tracing is the host's: a replay reads the host record in force when it starts.
            held = host.host_settings
            return held.reader.current().tracing if held is not None else config.tracing

        trace_replay = LeaseTraceReplay(
            leases=stores.lease_traces, exporter=exporter, identity=identity, clock=clock, config=replay_tracing
        )
        hub_token = held_hub_token(
            config, token, None if host.hub_tokens is None else RunnerHubTokenSource(host.hub_tokens, runner_id)
        )
        # This runner's own identity client: the host tick's watch reads on its thread, so no loop or app client is
        # shared. The held token is read per request; the runner closes the client with itself.
        identity_client = httpx2.Client(
            base_url=config.hub_url, timeout=_HOST_HUB_TIMEOUT_SECONDS, auth=HeldTokenAuth(hub_token)
        )
        platform_tracing.instrument_client(identity_client)
        return RunnerProcess(
            host=host,
            config=config,
            connections=connections,
            stores=stores,
            external_usage_attempts=ExternalUsageStore(connections),
            harness_sets=harness_sets,
            executor=executor,
            trace_settings=tracing,
            trace_export=trace,
            trace_sweep=trace_sweep,
            trace_replay=trace_replay,
            identity=identity,
            identity_verdict=IdentityVerdictHolder(_identity_remedy(config, runner_id)),
            retirement=RetiredRunnerWatch(
                runner_id=runner_id,
                rows=stores.identity,
                retirement=HubNoticedRetirement(
                    runner_id, host.host_stores.hosted_runners, host.clock, host.retirements
                ),
                token_identity=HttpHubClient(identity_client),
            ),
            hub_token=hub_token,
            identity_client=identity_client,
            events=RunnerEventPublisher.over(host.events, runner_id),
            flags=FlagsHolder(resolve_flags(host.flag_registry, "runner", config.flags).flags),
            harness_key=key_for,
        )
    except BaseException as failure:
        try:
            if identity_client is not None:
                identity_client.close()
        finally:
            executor.shutdown(wait=True)
            if trace is not None:
                try:
                    trace.close()
                except BaseException as cleanup:
                    failure.add_note(f"closing the runner trace exporter after opening also failed: {cleanup!r}")
        raise


def build_host_settings_application(host: HostProcess, reader: HostSettingsReader) -> HostSettingsApplication:
    """The host settings document's apply and export over ``host``'s store: its host-scoped records, and the record
    of each runner it holds when the apply runs, each over that runner's own connections. Judged through ``reader``
    and each runner's file entry."""
    connections = RunnerStoreConnections(host.engine, RunnerStoreErrorFactory(get_logger("blizzard.runner.store")))

    def readers() -> dict[str, tuple[RunnerProcess, RunnerSettingsReader]]:
        return {
            runner.runner_id: (runner, runner.settings_reader)
            for runner in host.runners
            if runner.settings_reader is not None
        }

    def entries() -> dict[str, RunnerEntry]:
        return {runner_id: reader.entry for runner_id, (_, reader) in readers().items()}

    unit = HostSettingsApplyStore(
        connections,
        settings=HostSettingsStore(connections, host.clock),
        workspaces=HostWorkspaceStore(connections, host.clock),
        subscriptions=HostSubscriptionStore(connections, host.clock),
        runners=lambda: {
            runner_id: RunnerSettingsStore(runner.connections, host.clock)
            for runner_id, (runner, _) in readers().items()
        },
    )
    return HostSettingsApplication(unit, HostApplyJudge(reader, entries), HOST_REGISTRY, host.clock, drivers=host.wakes)


def build_settings_stores(
    engine: Engine, *, errors: RunnerStoreErrorFactory, runner_id: str
) -> tuple[IRunnerIdentityStore, IWriteRunnerSettings]:
    """Construct the runner-bound identity and settings adapters for the CLI edge."""
    connections = RunnerStoreConnections(engine, errors, runner_id)
    return RunnerIdentityStore(connections), RunnerSettingsStore(connections, SystemClock())


def build_host_settings_store(engine: Engine, *, errors: RunnerStoreErrorFactory) -> IWriteHostSettings:
    """Construct the host's settings adapter for the CLI edge — the seed at a host's start."""
    return _build_host_stores(RunnerStoreConnections(engine, errors), SystemClock()).settings


def build_stores(engine: Engine, *, errors: RunnerStoreErrorFactory, runner_id: str | None) -> RunnerStores:
    """Construct and wire every extracted concept-store adapter over a migrated engine, bound to ``runner_id`` (or
    unbound, ``None``)."""
    return _build_stores(RunnerStoreConnections(engine, errors, runner_id))


def _build_stores(connections: RunnerStoreConnections, clock: IClock | None = None) -> RunnerStores:
    return RunnerStores(
        lease_record=LeaseRecordStore(connections),
        session=LeaseSessionStore(connections),
        liveness=LeaseLivenessStore(connections),
        resume_intent=LeaseResumeIntentStore(connections),
        environments=EnvironmentStore(connections),
        transcript_ledger=TranscriptLedgerStore(connections),
        tokens=TokenStore(connections),
        outbound=OutboundStore(connections),
        overload=OverloadStore(connections),
        asks=AskStore(connections),
        pause=PauseStore(connections),
        identity=RunnerIdentityStore(connections),
        settings=RunnerSettingsStore(connections, clock or SystemClock()),
        served_projects=ServedProjectsStore(connections),
        takeover=TakeoverStore(connections),
        requeue=RequeueStore(connections),
        escalations=EscalationStore(connections),
        usage=UsageStore(connections),
        attachments=AttachmentStore(connections),
        git_commit_declarations=GitCommitDeclarationStore(connections),
        lease_lock=LeaseLockStore(connections),
        checks=CheckStore(connections),
        elicitations=ElicitationStore(connections),
        invocation_boundaries=InvocationBoundaryStore(connections),
        selftest_results=SelfTestResultStore(connections),
        lease_traces=LeaseTraceFactsStore(connections),
        usage_limits=RunnerUsageLimitStore(connections),
        execution_observations=ExecutionObservationStore(connections),
    )


def build_runner_admission(
    engine: Engine, *, errors: RunnerStoreErrorFactory, runner_id: str, clock: IClock
) -> IRunnerAdmission:
    """The admission of ``runner_id`` into the store ``engine`` opens: its runner half bound to it, its host half naming
    it, the store's identity rows read through the runner audit for a store that hosts no runner yet."""
    connections = RunnerStoreConnections(engine, errors, runner_id)
    return RunnerAdmissionStore(
        connections,
        identity=RunnerIdentityStore(connections),
        settings=RunnerSettingsStore(connections, clock),
        hosted=HostHostedRunnerStore(connections),
        hub_tokens=HostHubTokenStore(connections),
        named_runners=RunnerAudit(connections).named_runners,
    )


@contextlib.contextmanager
def runner_admission(db_url: str, *, runner_id: str, clock: IClock) -> Generator[IRunnerAdmission, None, None]:
    """:func:`build_runner_admission` over its own engine, for ``runner init``'s join on a stopped host; disposes the
    engine on exit."""
    engine = create_engine_from_url(db_url)
    try:
        yield build_runner_admission(
            engine,
            errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")),
            runner_id=runner_id,
            clock=clock,
        )
    finally:
        engine.dispose()


@collaborator
@dataclass(frozen=True)
class _RunnerAdmissions:
    admit: Callable[[str], IRunnerAdmission]

    def admission(self, runner_id: str) -> IRunnerAdmission:
        return self.admit(runner_id)


def _recorded_name(recorded: Mapping[str, RecordedName], runner_id: str) -> str:
    """:func:`hosted_name` of what ``recorded`` holds of ``runner_id``."""
    name = recorded.get(runner_id, RecordedName())
    return hosted_name(name.stored, name.identity_name, runner_id)


@dataclass(frozen=True)
class _HostedRunnerNames:
    """A hosted runner's ``[[runner]]`` name, else its stored one, read for every runner at once."""

    host: HostConfig
    audit: IRunnerAudit

    def names_of(self, runner_ids: Sequence[str]) -> Mapping[str, str]:
        declared = {entry.id: entry.name for entry in self.host.runners if entry.id is not None}
        recorded = self.audit.recorded_names() if any(runner_id not in declared for runner_id in runner_ids) else {}
        return {runner_id: declared.get(runner_id) or _recorded_name(recorded, runner_id) for runner_id in runner_ids}

    def declared_names(self) -> frozenset[str]:
        return frozenset(entry.name for entry in self.host.runners)


@collaborator
@dataclass(frozen=True)
class _NewRunnerSettings:
    """A new runner's entries judged as a store-only runner's are, over the host as ``host`` reads it at the add —
    its workspaces and subscriptions as their records state them — before its hub mints its id."""

    host: Callable[[], HostConfig]

    def problems(self, name: str, entries: Mapping[str, Entry]) -> Mapping[str, str]:
        host = self.host()
        try:
            entry = host.hosted_entry(None, name)
        except (ConfigError, ValueError) as exc:
            return {"name": str(exc)}
        return RunnerEntriesJudge(host, entry).validate(entries)


@collaborator
@dataclass(frozen=True)
class _SealingTokens:
    """Seals each added runner's token under the host's key, minting its first generation when it holds none."""

    tokens: Callable[[], HubTokens]

    def seal_first(self, runner_id: str, token: str) -> SealedValue:
        return self.tokens().seal_first(runner_id, token)


def host_record_reads(stores: HostStores | HostReadStores) -> HostRecordReads:
    """The host's workspace and subscription records, as the host's settings reader reads them every pass."""
    return HostRecordReads(workspaces=stores.workspaces, subscriptions=stores.subscriptions)


def host_settings_reader(host: HostConfig, stores: HostStores | HostReadStores) -> HostSettingsReader:
    """The reader of ``host``'s stored entries and records over ``stores``."""
    return HostSettingsReader(host, stores.settings, host_record_reads(stores))


def build_host_record_services(
    reader: HostSettingsReader, stores: HostStores, *, clock: IClock
) -> Mapping[RecordKind, HostRecordService]:
    """Each record kind's service over ``stores``, judging a write by ``reader``'s host file parse beside the host's
    stored entries and records — a running host's own reader, so one resolution serves its passes and its writes."""
    validation = HostRecordValidation(reader)
    return {
        RecordKind.WORKSPACE: HostRecordService(RecordKind.WORKSPACE, stores.workspaces, validation, clock),
        RecordKind.SUBSCRIPTION: HostRecordService(RecordKind.SUBSCRIPTION, stores.subscriptions, validation, clock),
    }


def _file_records(config: HostConfig, stores: HostStores | HostReadStores) -> tuple[HostRecord, ...]:
    """Each ``[[workspace]]`` and ``[[subscription]]`` table as the record a seed makes of it, whole from its table,
    the first of a key only. A deprecated ``workspace_prompt`` override for a workspace is its ``prompt``, with
    ``prompt_file`` and ``prompt_package`` at their defaults, since the override already won over every file
    prompt."""
    authored = config.authored
    if authored is None:
        return ()
    records: dict[tuple[RecordKind, str], HostRecord] = {}
    for kind in (RecordKind.WORKSPACE, RecordKind.SUBSCRIPTION):
        tables = cast(object, authored.get(str(kind), []))
        for table in cast(list[object], tables) if isinstance(tables, list) else []:
            if not isinstance(table, dict):
                continue
            entry = cast(dict[str, object], table)
            if not isinstance(key := entry.get(kind.key_name), str) or (kind, key.strip()) in records:
                continue
            settings = {name: value for name, value in entry.items() if name != kind.key_name}
            if kind is RecordKind.WORKSPACE:
                override = stores.workspace_prompt.workspace_prompt_override(key.strip())
                if override is not None:
                    settings = PromptChange.stating("prompt", override).applied_to(settings)
            records[kind, key.strip()] = HostRecord(kind, key.strip(), 0, settings)
    return tuple(records.values())


def seed_record_tables(config: HostConfig, stores: HostStores, *, clock: IClock) -> None:
    """Seed steps 2-3: each ``[[workspace]]`` and ``[[subscription]]`` table with no record becomes one
    (:func:`_file_records`), each its own write under the seed's door and actor; a record the store holds, retired
    or not, is never touched."""
    services = build_host_record_services(host_settings_reader(config, stores), stores, clock=clock)
    for record in _file_records(config, stores):
        services[record.kind].seed(record.key, record.settings)


def seeded_records(config: HostConfig, stores: HostStores | HostReadStores) -> HostRecordsSnapshot:
    """The host's records as they stand once its file's tables are seeded (:func:`seed_record_tables`), read with no
    write: for a verb that judges against them and must leave the store as it found it when it refuses."""
    workspaces = stores.workspaces.records(retired=True)
    subscriptions = stores.subscriptions.records()
    held = {(record.kind, record.key) for record in workspaces} | {
        (RecordKind.SUBSCRIPTION, record.key) for record in stores.subscriptions.records(retired=True)
    }
    unseeded = [record for record in _file_records(config, stores) if (record.kind, record.key) not in held]

    def ordered(records: Sequence[HostRecord]) -> tuple[HostRecord, ...]:
        return tuple(sorted(records, key=lambda record: record.key))

    return HostRecordsSnapshot(
        workspaces=ordered(
            [r for r in workspaces if not r.retired] + [r for r in unseeded if r.kind is RecordKind.WORKSPACE]
        ),
        subscriptions=ordered([*subscriptions, *(r for r in unseeded if r.kind is RecordKind.SUBSCRIPTION)]),
        retired_workspaces=tuple(r for r in workspaces if r.retired),
    )


def held_records(config: HostConfig, stores: HostStores | HostReadStores, kind: RecordKind) -> tuple[HostRecord, ...]:
    """Every record of ``kind`` the host holds, retired ones included, once its file's tables are seeded — each one
    the store holds none of at the revision its seed writes — read with no write, so a stopped host's reads show what
    its next start serves."""
    held = stores.records_of(kind).records(retired=True)
    keys = {record.key for record in held}
    unseeded = [
        replace(record, revision=1)
        for record in _file_records(config, stores)
        if record.kind is kind and record.key not in keys
    ]
    return tuple(sorted((*held, *unseeded), key=lambda record: record.key))


def recorded_host_config(
    config: HostConfig, stores: HostStores | HostReadStores, records: HostRecordsSnapshot | None = None
) -> HostConfig:
    """``config`` with its workspaces and subscriptions the store's active records — or ``records`` — over its stored
    entries; raises :class:`ValueError` when they do not resolve."""
    reader = HostSettingsReader(config, stores.settings, host_record_reads(stores))
    if records is None:
        return reader.current()
    held = stores.settings.snapshot()
    return reader.resolve(HostSettingsSnapshot(held.revision, held.entries, records))


def build_hosted_runner_service(
    host: HostConfig,
    engine: Engine,
    hub: IHostRunnerHub,
    *,
    clock: IClock,
    environ: Mapping[str, str] | None = None,
) -> HostedRunnerService:
    """The service that adds and retires a stopped host's runners in the store ``engine`` opens, under the host's
    token through ``hub``, over host stores and a token sealer of its own; a new runner is judged against ``host``,
    resolved from the store's records."""
    stores = build_host_stores(engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")), clock=clock)
    sealer = functools.partial(
        _sealing_hub_tokens,
        stores.hub_tokens,
        host.root,
        environ=os.environ if environ is None else environ,
        clock=clock,
    )
    return _hosted_runner_service(host, engine, hub, stores, sealer, clock, judged=lambda: host)


def build_running_host_runner_service(
    host_config: HostConfig, host: HostProcess, hub: IHostRunnerHub, *, environ: Mapping[str, str] | None = None
) -> HostedRunnerService:
    """The service that adds and retires a running ``host``'s runners, under the host's token through ``hub``, over
    the host's own stores and token sealer."""
    return _hosted_runner_service(
        host_config,
        host.engine,
        hub,
        host.host_stores,
        functools.partial(
            _running_host_tokens, host, host_config.root, environ=os.environ if environ is None else environ
        ),
        host.clock,
        judged=host.host_settings.reader.current if host.host_settings is not None else lambda: host_config,
    )


def _running_host_tokens(host: HostProcess, root: Path, *, environ: Mapping[str, str]) -> HubTokens:
    """The running host's sealed tokens; on a host that holds no key yet, its first generation minted to seal under."""
    if host.hub_tokens is not None:
        return host.hub_tokens
    return _sealing_hub_tokens(host.host_stores.hub_tokens, root, environ=environ, clock=host.clock)


def _hosted_runner_service(
    host: HostConfig,
    engine: Engine,
    hub: IHostRunnerHub,
    stores: HostStores,
    tokens: Callable[[], HubTokens],
    clock: IClock,
    *,
    judged: Callable[[], HostConfig],
) -> HostedRunnerService:
    errors = RunnerStoreErrorFactory(get_logger("blizzard.runner.store"))
    return HostedRunnerService(
        hosted=stores.hosted_runners,
        admissions=_RunnerAdmissions(functools.partial(_admission_of, engine, errors=errors, clock=clock)),
        settings=_NewRunnerSettings(judged),
        names=_HostedRunnerNames(host, stores.runner_audit),
        tokens=_SealingTokens(tokens),
        hub=hub,
        clock=clock,
    )


def _admission_of(
    engine: Engine, runner_id: str, *, errors: RunnerStoreErrorFactory, clock: IClock
) -> IRunnerAdmission:
    return build_runner_admission(engine, errors=errors, runner_id=runner_id, clock=clock)


def build_read_stores(engine: Engine, *, errors: RunnerStoreErrorFactory, runner_id: str | None) -> RunnerReadStores:
    """The read-only bundle a controller-facing collaborator takes — narrows build_stores's
    bundle over the same adapter instances, never a second one — bound to the configured ``runner_id``, or unbound
    (reading as empty) for a runner that has none yet."""
    return RunnerReadStores.of(build_stores(engine, errors=errors, runner_id=runner_id))


def build_host_read_stores(engine: Engine, *, errors: RunnerStoreErrorFactory) -> HostReadStores:
    """The read-only host-store bundle a short-lived CLI verb takes."""
    return HostReadStores.of(build_host_stores(engine, errors=errors, clock=SystemClock()))


def build_runner_transfer(
    source: Engine, destination: Engine, *, errors: RunnerStoreErrorFactory, runner_id: str, clock: IClock
) -> IRunnerTransfer:
    """The copy of ``runner_id`` from the migrated ``source`` store into ``destination``: both stores' connections bound
    to it, and the destination's host member running the host half on the copy's transaction."""
    into = RunnerStoreConnections(destination, errors, runner_id)
    return RunnerTransferStore(
        RunnerStoreConnections(source, errors, runner_id),
        into,
        _build_host_stores(into, clock).host_ingests,
        HostHostedRunnerStore(into),
        HostHubTokenStore(into),
        RunnerAudit(into).named_runners,
    )


def build_runner_removal(engine: Engine, *, errors: RunnerStoreErrorFactory, runner_id: str) -> IRunnerRowRemoval:
    """The removal of ``runner_id``'s rows from the store ``engine`` opens, its connections bound to it."""
    store = RunnerStoreConnections(engine, errors, runner_id)
    return RunnerRowRemovalStore(store, HostHostedRunnerStore(store), HostHubTokenStore(store))


@dataclass(frozen=True)
class RunnerStoreFiles:
    """The offline verbs' reach into store files by URL — each call opens its engines and disposes of them before it
    returns, so a verb holds no store open between its steps."""

    errors: RunnerStoreErrorFactory
    clock: IClock

    def reads_for(self, runner_id: str) -> RunnerBoundStoreFiles:
        return RunnerBoundStoreFiles(self, runner_id)

    def for_runner(self, runner_id: str) -> RunnerBoundStoreFiles:
        return RunnerBoundStoreFiles(self, runner_id)

    def named_runners(self, url: str) -> tuple[str, ...]:
        with _engine(url) as engine:
            return build_host_stores(engine, errors=self.errors, clock=self.clock).runner_audit.named_runners()

    def host_roster(self, url: str) -> HostRoster:
        """The store at ``url``'s runners, read once: its identity rows, hosted runners and their stored names."""
        with _engine(url) as engine:
            stores = build_host_stores(engine, errors=self.errors, clock=self.clock)
            hosted = stores.hosted_runners
            recorded = stores.runner_audit.recorded_names()
            return HostRoster(
                named=stores.runner_audit.named_runners(),
                hosted=hosted.hosted_runner_ids(),
                split=hosted.roster(),
                names={runner_id: _recorded_name(recorded, runner_id) for runner_id in recorded},
                beside_the_file=beside_the_file_of(hosted.all_hosted()),
            )

    def host_identity(self, url: str) -> HostIdentity | None:
        with _engine(url) as engine:
            return build_host_read_stores(engine, errors=self.errors).host_identity.host_identity()

    def sealed_key_ids(self, url: str) -> frozenset[str]:
        with _engine(url) as engine:
            return build_host_read_stores(engine, errors=self.errors).hub_tokens.key_ids()

    def held_bindings(self, url: str) -> tuple[HeldBinding, ...]:
        with _engine(url) as engine:
            named = build_host_stores(engine, errors=self.errors, clock=self.clock).runner_audit.named_runners()
            return tuple(
                HeldBinding(runner_id, binding)
                for runner_id in named
                for binding in build_read_stores(
                    engine, errors=self.errors, runner_id=runner_id
                ).environments.held_bindings()
            )


@dataclass(frozen=True)
class RunnerBoundStoreFiles:
    """:class:`RunnerStoreFiles` bound to one runner."""

    files: RunnerStoreFiles
    runner_id: str

    def copy_runner(
        self,
        source_url: str,
        destination_url: str,
        *,
        workspace_ids: Mapping[str, str] | None = None,
        bound_workspaces: Sequence[str] = (),
        ingest: HostIngest | None = None,
        hub_token: SealedValue | None = None,
    ) -> None:
        with _engine(source_url) as source, _engine(destination_url) as destination:
            build_runner_transfer(
                source, destination, errors=self.files.errors, runner_id=self.runner_id, clock=self.files.clock
            ).copy_runner(
                workspace_ids=workspace_ids, bound_workspaces=bound_workspaces, ingest=ingest, hub_token=hub_token
            )

    def remove_runner_rows(self, url: str, *, keep_counters: bool, drop_host: bool = False) -> None:
        with _engine(url) as engine:
            build_runner_removal(engine, errors=self.files.errors, runner_id=self.runner_id).remove_runner_rows(
                keep_counters=keep_counters, drop_host=drop_host
            )

    def runner_view(self, url: str) -> RunnerStoreView:
        errors, clock = self.files.errors, self.files.clock
        with _engine(url) as engine:
            runner = build_read_stores(engine, errors=errors, runner_id=self.runner_id)
            host = build_host_stores(engine, errors=errors, clock=clock)
            identity = runner.identity.runner_identity()
            return RunnerStoreView(
                named_runners=host.runner_audit.named_runners(),
                tenant_id=identity.tenant_id if identity is not None else None,
                open_elicitation_lease_ids=frozenset(runner.elicitations.in_flight_elicitation_lease_ids()),
                open_takeover_chunk_ids=frozenset(runner.takeover.open_takeover_chunk_ids()),
                held_bindings=tuple(runner.environments.held_bindings()),
                last_alive_at=host.daemon_liveness.last_daemon_liveness(),
                served_projects=_served_slugs(runner.served_projects.served_projects_answer()),
            )

    def latest_host_ingest(self, url: str) -> HostIngest | None:
        with _engine(url) as engine:
            return build_host_read_stores(engine, errors=self.files.errors).host_ingests.latest_host_ingest(
                self.runner_id
            )

    def sealed_hub_token(self, url: str) -> SealedHubToken | None:
        with _engine(url) as engine:
            return build_host_read_stores(engine, errors=self.files.errors).hub_tokens.sealed(self.runner_id)


def _served_slugs(answer: ServedProjectsAnswer | None) -> tuple[str, ...]:
    if answer is None:
        return ()
    return tuple(p.slug or p.entry for p in answer.projects if p.resolved())


def build_runner_store_files(clock: IClock | None = None) -> RunnerStoreFiles:
    """The store-file seams an offline verb moves a runner's rows through."""
    return RunnerStoreFiles(RunnerStoreErrorFactory(get_logger("blizzard.runner.store")), clock or SystemClock())


@contextlib.contextmanager
def _engine(url: str) -> Generator[Engine, None, None]:
    engine = create_engine_from_url(url)
    try:
        yield engine
    finally:
        engine.dispose()


_runner_bound_stores_conforms: type[IRunnerBoundStores] = RunnerBoundStoreFiles


_ingest_stores_conforms: type[IIngestStores] = RunnerStoreFiles


@collaborator
@dataclass(frozen=True)
class DotenvTokenFiles:
    """Runner tokens in host directories' ``.env`` files."""

    def held(self, root: Path, token_env: str) -> str:
        return HubTokenFile.of(root, token_env).held()

    def remove(self, root: Path, token_env: str) -> None:
        HubTokenFile.of(root, token_env).remove()


_token_files_conforms: type[ITokenFiles] = DotenvTokenFiles


@collaborator
@dataclass(frozen=True)
class HostKeyCiphers:
    """The ciphers under a host directory's key: read from ``environ`` first and its ``data/secret-keys/``
    otherwise, as its service resolves it; a source's own directory key comes before ``environ``'s."""

    environ: Mapping[str, str]

    def cipher(self, root: Path) -> ISecretCipher | None:
        keys = host_key_provider(self.environ, HostLayout(root), mint=False)
        return None if keys is None else secret_cipher(keys)

    def sealing_cipher(self, root: Path, *, sealed_under: frozenset[str]) -> ISecretCipher:
        return secret_cipher(sealing_key_provider(self.environ, HostLayout(root), sealed_under=sealed_under))

    def source_ciphers(self, root: Path) -> tuple[ISecretCipher, ...]:
        layers = (
            host_key_provider({}, HostLayout(root), mint=False),
            host_key_provider(self.environ, HostLayout(root), mint=False)
            if self.environ.get(ENV_HOST_SECRET_KEY)
            else None,
        )
        return tuple(secret_cipher(keys) for keys in layers if keys is not None)


_host_ciphers_conforms: type[IHostCiphers] = HostKeyCiphers


def declared_workspace(workspace: HostWorkspace, host_root: Path) -> DeclaredWorkspace:
    """``workspace`` as the transfer declaration of it, its root resolved against ``host_root``, the host that declared
    it."""
    return DeclaredWorkspace(
        id=workspace.id,
        root=workspace.root,
        provider=workspace.provider,
        envs=workspace.envs,
        max_environments=workspace.max_environments,
        repos=workspace.repos,
        base_branch=workspace.base_branch,
        prompt=workspace.prompt,
        prompt_file=workspace.prompt_file,
        prompt_package=workspace.prompt_package,
        effective_root=workspace.settings(host_root).effective_workspace_root,
    )


def declared_subscription(subscription: SubscriptionDeclaration) -> DeclaredSubscription:
    return DeclaredSubscription(
        slug=subscription.slug,
        name=subscription.name,
        provider=subscription.provider,
        credentials_path=subscription.credentials_path,
        sample_interval_seconds=subscription.sample_interval_seconds,
        usage_api_base=subscription.usage_api_base,
    )


def host_declarations(config: HostConfig) -> HostDeclarations:
    """What ``config``'s host file declares, as transfer values: the one mapping a host's declarations take, for this
    host and for a source alike."""
    return HostDeclarations(
        root=config.root,
        config_path=config.config_path,
        runners=tuple(
            RunnerDeclaration(
                name=entry.name,
                id=entry.id,
                subscription=entry.subscription,
                bindings=tuple(DeclaredBinding(b.project, b.workspace) for b in entry.bindings),
                errors=entry.errors,
            )
            for entry in config.runners
        ),
        workspaces=tuple(declared_workspace(workspace, config.root) for workspace in config.workspaces),
        subscriptions=tuple(declared_subscription(subscription) for subscription in config.subscriptions),
    )


def ingest_host(config: HostConfig) -> IngestHost:
    """The host ``config`` loads, as the host an ingest runs into."""
    return IngestHost(
        root=config.root,
        data_dir=config.layout.data_dir,
        runners_dir=config.layout.runners_dir,
        db_url=config.db_url,
        hub_url=config.hub_url,
        holds_host_token=bool(config.host_token.value),
        declared=host_declarations(config),
    )


@domain_model
@dataclass(frozen=True)
class _OpenSource:
    """A source's loaded host, and the text of its host file when that is not on disk."""

    config: HostConfig
    host_text: str | None = None


@collaborator
@dataclass(frozen=True)
class ConfigSourceHosts:
    """Source hosts read through the host loader, for the host ``host``. A source it has opened is kept by path, so the
    runner it resolves and the ``[[runner]]`` it carries are the ones its loaded configuration names."""

    host: HostConfig
    _opened: dict[Path, _OpenSource] = field(default_factory=dict[Path, _OpenSource])

    def kind(self, source: Path) -> SourceKind:
        directory = None
        is_file = False
        header = b""
        if source.is_dir():
            directory = DirectoryState(HostLayout(source).graduation().value)
        elif source.is_file():
            is_file = True
            with source.open("rb") as handle:
                header = handle.read(len(SQLITE_HEADER))
        return classify_source(
            str(source), directory=directory, is_file=is_file, header=header, host_config_filename=HOST_CONFIG_FILENAME
        )

    def legacy_socket(self, root: Path) -> Path:
        return HostLayout(root).legacy_socket_path

    def open_host(self, path: Path) -> SourceHost:
        try:
            config = HostConfig.load(path, flag_registry=self.host.flag_registry, strict=self.host.strict)
            store = sqlite_store_file(config.db_url)
        except (ConfigError, StoreResetRefused) as exc:
            raise SourceUnsupported(f"cannot read the source host at {path}: {exc}") from exc
        self._opened[path] = _OpenSource(config)
        return SourceHost(SourceKind.HOST_DIRECTORY, path, store, host_declarations(config))

    def legacy_store(self, path: Path) -> tuple[Path, str]:
        layout = HostLayout(path)
        store = layout.legacy_store_path
        try:
            legacy = read_legacy_document(layout)
        except ConfigError as exc:
            raise SourceUnsupported(str(exc)) from exc
        named = legacy.get("db_url")
        if named is not None and sqlite_url(store) != named and f"sqlite:///{store}" != named:
            raise SourceUnsupported(
                f"{layout.legacy_config_path} names its store at {named}, not {store}: graduation reads the "
                f"directory's own store only, so graduate it by hand ({GRADUATION_PROCEDURE})"
            )
        return store, str(legacy.get("token_env", UNSUFFIXED_TOKEN_VARIABLE))

    def graduate(self, path: Path, *, runner_id: str, served: tuple[str, ...]) -> SourceHost:
        layout = HostLayout(path)
        store, token_env = self.legacy_store(path)
        try:
            config = HostConfig.graduated(
                path,
                read_legacy_document(layout),
                runner_id=runner_id,
                served=served,
                flag_registry=self.host.flag_registry,
                strict=self.host.strict,
            )
        except ConfigError as exc:
            raise SourceUnsupported(f"cannot graduate the runner file {layout.legacy_config_path}: {exc}") from exc
        self._opened[path] = _OpenSource(config, config.to_toml())
        return SourceHost(SourceKind.UNGRADUATED, path, store, host_declarations(config), token_env=token_env)

    def runner(self, source: SourceHost | None, name: str) -> RunnerDeclaration:
        config = self.host if source is None else self._open(source).config
        entry = config.sole_runner(name)
        return next(declared for declared in host_declarations(config).runners if declared.name == entry.name)

    def path_keys(self, source: SourceHost, runner: str) -> Mapping[tuple[str, ...], Path]:
        opened = self._open(source)
        table = self._table(opened, runner)
        found: dict[tuple[str, ...], Path] = {}
        for path_key in RUNNER_PATH_KEYS:
            value = runner_value(table, path_key.key, strict=self.host.strict)
            if isinstance(value, str) and value:
                located = Path(value).expanduser()
                found[path_key.key] = (located if located.is_absolute() else opened.config.root / located).resolve()
        return found

    def carried(
        self,
        source: SourceHost,
        runner: str,
        *,
        projects: tuple[DeclaredBinding, ...] | None,
        copies: Mapping[tuple[str, ...], Path],
    ) -> str:
        table = self._table(self._open(source), runner)
        values: dict[tuple[str, ...], str] = {}
        if projects is not None:
            values[("projects",)] = bindings_literal(tuple(ProjectBinding(b.project, b.workspace) for b in projects))
        for key, copied in copies.items():
            values[key] = string_literal(str(copied))
        try:
            return with_runner_values(table, values, strict=self.host.strict)
        except ConfigError as exc:
            raise SourceUnsupported(f"runner {runner}'s [[runner]] cannot be carried: {exc}") from exc

    def _open(self, source: SourceHost) -> _OpenSource:
        try:
            return self._opened[source.path]
        except KeyError:
            raise LookupError(
                f"{source.path} was not opened by this port; open it with open_host or graduate"
            ) from None

    def _table(self, opened: _OpenSource, runner: str) -> str:
        try:
            return runner_table(opened.config.layout, runner, strict=self.host.strict, text=opened.host_text)
        except ConfigError as exc:
            raise SourceUnsupported(str(exc)) from exc


_source_hosts_conforms: type[ISourceHosts] = ConfigSourceHosts


@collaborator
@dataclass(frozen=True)
class SeededHereRecords:
    """This host's workspaces and subscriptions from its store, as they stand once its file's tables are seeded."""

    host: HostConfig

    def held(self) -> HeldRecords:
        engine = create_engine_from_url(self.host.db_url)
        try:
            stores = build_host_read_stores(engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")))
            records = seeded_records(self.host, stores)
            try:
                recorded = recorded_host_config(self.host, stores, records)
            except ValueError as exc:
                raise ConfigError(str(exc)) from exc
        finally:
            engine.dispose()
        return HeldRecords(
            workspaces=tuple(declared_workspace(workspace, recorded.root) for workspace in recorded.workspaces),
            subscriptions=tuple(declared_subscription(subscription) for subscription in recorded.subscriptions),
            retired_workspace_ids=frozenset(record.key for record in records.retired_workspaces),
        )


_here_records_conforms: type[IHereRecords] = SeededHereRecords


def build_runner_ingest(
    host: HostConfig,
    hub: IHostHubClient,
    *,
    holder: Callable[[Path], str | None],
    clock: IClock | None = None,
    migrations: Callable[[str], MigrationRunner] = runner_store_migrations,
    files: IHostFiles | None = None,
    environ: Mapping[str, str] | None = None,
) -> RunnerIngest:
    """The ingest into the host ``host``, reaching its hub through ``hub``, probing sockets with ``holder``,
    migrating each source copy through ``migrations``, and reading each host's key from ``environ`` — the process
    environment by default — before its key directory."""
    resolved = clock or SystemClock()
    return RunnerIngest(
        host=ingest_host(host),
        sources=ConfigSourceHosts(host),
        stores=build_runner_store_files(resolved),
        host_file=HostFileEditor(strict=host.strict),
        tokens=DotenvTokenFiles(),
        ciphers=HostKeyCiphers(os.environ if environ is None else environ),
        hub=hub,
        holder=holder,
        files=files
        or HostFiles(
            locate_store=sqlite_store_file,
            not_a_store_file=StoreResetRefused,
            url_of=sqlite_url,
            set_aside_store=set_aside_sqlite,
            layout_of=HostLayout,
            hold_host=hold_host,
        ),
        clock=resolved,
        migrations=migrations,
        records=SeededHereRecords(host),
    )
