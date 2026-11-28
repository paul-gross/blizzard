"""Runner host-store registrations over borrowed connections and clock."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine

from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.foundation.roles import adapter_model
from blizzard.runner.process.probe import IProcessProbe
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.store.internal.host_daemon_liveness_store import DaemonLivenessStore
from blizzard.runner.store.internal.host_external_usage_store import ExternalUsageStore
from blizzard.runner.store.internal.host_graph_artifact_store import GraphArtifactStore
from blizzard.runner.store.internal.host_hosted_runner_store import HostHostedRunnerStore
from blizzard.runner.store.internal.host_hub_token_store import HostHubTokenStore
from blizzard.runner.store.internal.host_identity_store import HostIdentityStore
from blizzard.runner.store.internal.host_ingest_store import HostIngestStore
from blizzard.runner.store.internal.host_jti_cache_store import JtiCacheRepository
from blizzard.runner.store.internal.host_lane_store import HostLaneStore
from blizzard.runner.store.internal.host_record_store import HostSubscriptionStore, HostWorkspaceStore
from blizzard.runner.store.internal.host_runner_audit import RunnerAudit
from blizzard.runner.store.internal.host_settings_store import HostSettingsStore
from blizzard.runner.store.internal.host_usage_limit_store import SubscriptionUsageLimitStore
from blizzard.runner.store.internal.host_workspace_prompt_store import WorkspacePromptStore
from blizzard.runner.store.internal.invariants import RunnerInvariants
from blizzard.runner.store.scoped import RunnerStoreConnections
from blizzard.runner.stores import HostStores, IRunnerAudit


def assemble_host_stores(
    workspace_prompt: WorkspacePromptStore,
    graph_artifacts: GraphArtifactStore,
    jti_cache: JtiCacheRepository,
    daemon_liveness: DaemonLivenessStore,
    external_usage: ExternalUsageStore,
    host_lane: HostLaneStore,
    host_identity: HostIdentityStore,
    host_ingests: HostIngestStore,
    hub_tokens: HostHubTokenStore,
    hosted_runners: HostHostedRunnerStore,
    usage_limits: SubscriptionUsageLimitStore,
    settings: HostSettingsStore,
    workspaces: HostWorkspaceStore,
    subscriptions: HostSubscriptionStore,
    runner_audit: RunnerAudit,
) -> HostStores:
    """Collect registered adapters without constructing another instance."""
    return HostStores(
        workspace_prompt=workspace_prompt,
        graph_artifacts=graph_artifacts,
        jti_cache=jti_cache,
        daemon_liveness=daemon_liveness,
        external_usage=external_usage,
        host_lane=host_lane,
        host_identity=host_identity,
        host_ingests=host_ingests,
        hub_tokens=hub_tokens,
        hosted_runners=hosted_runners,
        usage_limits=usage_limits,
        settings=settings,
        workspaces=workspaces,
        subscriptions=subscriptions,
        runner_audit=runner_audit,
    )


def host_stores(connections: RunnerStoreConnections, clock: IClock) -> HostStores:
    """Compatibility builder using the same adapter constructors as the registry."""
    return assemble_host_stores(
        workspace_prompt=WorkspacePromptStore(connections),
        graph_artifacts=GraphArtifactStore(connections),
        jti_cache=JtiCacheRepository(connections, clock),
        daemon_liveness=DaemonLivenessStore(connections),
        external_usage=ExternalUsageStore(connections),
        host_lane=HostLaneStore(connections),
        host_identity=HostIdentityStore(connections),
        host_ingests=HostIngestStore(connections),
        hub_tokens=HostHubTokenStore(connections),
        hosted_runners=HostHostedRunnerStore(connections),
        usage_limits=SubscriptionUsageLimitStore(connections),
        settings=HostSettingsStore(connections, clock),
        workspaces=HostWorkspaceStore(connections, clock),
        subscriptions=HostSubscriptionStore(connections, clock),
        runner_audit=build_runner_audit(connections),
    )


def build_runner_audit(connections: RunnerStoreConnections) -> RunnerAudit:
    """The cross-runner audit over the borrowed seam."""
    return RunnerAudit(connections)


def runner_invariant_checker(audit: IRunnerAudit, process: IProcessProbe) -> RunnerInvariants:
    """The bounded checker over its audit and caller-supplied process probe."""
    return RunnerInvariants(audit, process)


@adapter_model
@dataclass(frozen=True)
class HostStoreRefs:
    """The host adapters concept areas alias onto their own ports."""

    workspace_prompt: Ref[WorkspacePromptStore]
    graph_artifacts: Ref[GraphArtifactStore]
    jti_cache: Ref[JtiCacheRepository]
    daemon_liveness: Ref[DaemonLivenessStore]
    external_usage: Ref[ExternalUsageStore]
    host_lane: Ref[HostLaneStore]
    host_identity: Ref[HostIdentityStore]
    host_ingests: Ref[HostIngestStore]
    hub_tokens: Ref[HostHubTokenStore]
    hosted_runners: Ref[HostHostedRunnerStore]
    usage_limits: Ref[SubscriptionUsageLimitStore]
    settings: Ref[HostSettingsStore]
    workspaces: Ref[HostWorkspaceStore]
    subscriptions: Ref[HostSubscriptionStore]
    runner_audit: Ref[RunnerAudit]
    bundle: Ref[HostStores]


def register_host_stores(registry: Registry) -> HostStoreRefs:
    """Declare the host adapters and checker, handing their refs to the process root."""
    workspace_prompt = registry.service(WorkspacePromptStore, lifetime=Lifetime.PROCESS)
    graph_artifacts = registry.service(GraphArtifactStore, lifetime=Lifetime.PROCESS)
    jti_cache = registry.service(JtiCacheRepository, lifetime=Lifetime.PROCESS)
    daemon_liveness = registry.service(DaemonLivenessStore, lifetime=Lifetime.PROCESS)
    external_usage = registry.service(ExternalUsageStore, lifetime=Lifetime.PROCESS)
    host_lane = registry.service(HostLaneStore, lifetime=Lifetime.PROCESS)
    host_identity = registry.service(HostIdentityStore, lifetime=Lifetime.PROCESS)
    host_ingests = registry.service(HostIngestStore, lifetime=Lifetime.PROCESS)
    hub_tokens = registry.service(HostHubTokenStore, lifetime=Lifetime.PROCESS)
    hosted_runners = registry.service(HostHostedRunnerStore, lifetime=Lifetime.PROCESS)
    usage_limits = registry.service(SubscriptionUsageLimitStore, lifetime=Lifetime.PROCESS)
    settings = registry.service(HostSettingsStore, lifetime=Lifetime.PROCESS)
    workspaces = registry.service(HostWorkspaceStore, lifetime=Lifetime.PROCESS)
    subscriptions = registry.service(HostSubscriptionStore, lifetime=Lifetime.PROCESS)
    runner_audit = registry.factory(build_runner_audit, lifetime=Lifetime.PROCESS)
    registry.alias(port(IRunnerAudit), runner_audit)
    registry.factory(runner_invariant_checker, lifetime=Lifetime.PROCESS)
    bundle = registry.factory(assemble_host_stores, lifetime=Lifetime.PROCESS)
    return HostStoreRefs(
        workspace_prompt=workspace_prompt,
        graph_artifacts=graph_artifacts,
        jti_cache=jti_cache,
        daemon_liveness=daemon_liveness,
        external_usage=external_usage,
        host_lane=host_lane,
        host_identity=host_identity,
        host_ingests=host_ingests,
        hub_tokens=hub_tokens,
        hosted_runners=hosted_runners,
        usage_limits=usage_limits,
        settings=settings,
        workspaces=workspaces,
        subscriptions=subscriptions,
        runner_audit=runner_audit,
        bundle=bundle,
    )


def build_host_stores(
    engine: Engine, *, errors: RunnerStoreErrorFactory, clock: IClock, runner_id: str | None = None
) -> HostStores:
    """Build host stores over a borrowed engine, optionally binding cross-kind writes to a runner."""
    return host_stores(RunnerStoreConnections(engine, errors, runner_id), clock)
