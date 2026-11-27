"""The registration step — register at the hub, record the identity and served projects its reply names, seed the
runner's settings, and mirror the hub's pause brake.

The hub resolves who this runner is from its bearer token, and a registration never adopts an id: a reply naming
another runner writes nothing and turns the verdict to a mismatch. A failed registration leaves what stood, and a
refused version halts the runner alone until an admitted registration lifts it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.foundation.settings_record.refusals import SettingsInvalid
from blizzard.runner.environments.workspaces import IWorkspaceHomes
from blizzard.runner.harness.capability_snapshot import HarnessCapability
from blizzard.runner.hub.client import (
    BindingDeclaration,
    DecliningDeclaration,
    HubClientError,
    IHubClient,
    RunnerVersionRefused,
    SubscriptionDeclaration,
)
from blizzard.runner.hub.hub_version import HubVersionHolder
from blizzard.runner.hub.identity import (
    ICurrentRunnerIdentity,
    IdentityVerdictHolder,
    IWriteRunnerIdentityRepository,
    RunnerIdentity,
    RunnerIdentityHolder,
    answered_tenant,
    judged,
)
from blizzard.runner.hub.served_projects import IWriteServedProjectsRepository
from blizzard.runner.lifecycle.identity_check import log_transition
from blizzard.runner.throttle.gate import GateConfig, GateContext, GateStores, GateSubscription, ThrottleGate
from blizzard.runner.throttle.pause import IWritePauseRepository, RunnerBrakes
from blizzard.runner.throttle.report import declining_report

_log = get_logger("blizzard.runner.loop")


def registered_runner_id(identity: ICurrentRunnerIdentity) -> str | None:
    """This runner's hub-minted id, or ``None`` before its first successful registration — when
    nothing may be claimed and no route may be judged ours or another runner's."""
    current = identity.current()
    return current.runner_id if current is not None else None


class DeclaredSubscription(GateSubscription, Protocol):
    """One subscription the registration declares to the hub."""

    @property
    def slug(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def provider(self) -> str: ...


class RegistrationStores(GateStores, Protocol):
    @property
    def identity(self) -> IWriteRunnerIdentityRepository: ...
    @property
    def served_projects(self) -> IWriteServedProjectsRepository: ...
    @property
    def pause(self) -> IWritePauseRepository: ...


class ISeedRunnerSettings(Protocol):
    def run(self) -> None:
        """Seed the bound runner's record from its file entry, never overwriting a held entry. Raises
        :class:`SettingsInvalid` or ``ValueError`` on a refusal, writing nothing."""
        ...


class RegistrationConfig(GateConfig, Protocol):
    @property
    def runner_id(self) -> str: ...
    @property
    def runner_name(self) -> str: ...
    @property
    def bound_workspace_ids(self) -> tuple[str, ...]: ...
    @property
    def public_url(self) -> str: ...
    @property
    def redirect_uris(self) -> tuple[str, ...]: ...
    @property
    def gates(self) -> tuple[str, ...]: ...
    @property
    def projects(self) -> tuple[str, ...] | None: ...
    @property
    def bindings(self) -> tuple[BindingDeclaration, ...] | None: ...
    @property
    def agents(self) -> int: ...
    @property
    def subscription(self) -> str | None: ...
    @property
    def declares_subscriptions(self) -> bool: ...


class RegistrationContext(GateContext, Protocol):
    """What the registration step reads and writes — and the throttle gate's readings, from which it reports why the
    runner is declining."""

    @property
    def stores(self) -> RegistrationStores: ...
    @property
    def clock(self) -> IClock: ...
    @property
    def hub(self) -> IHubClient: ...
    @property
    def identity(self) -> RunnerIdentityHolder: ...
    @property
    def identity_verdict(self) -> IdentityVerdictHolder: ...
    @property
    def hub_version(self) -> HubVersionHolder: ...
    @property
    def config(self) -> RegistrationConfig: ...
    @property
    def subscriptions(self) -> Sequence[DeclaredSubscription]: ...
    @property
    def workspaces(self) -> IWorkspaceHomes: ...
    @property
    def settings_seed(self) -> ISeedRunnerSettings | None:
        """The seed of this runner's settings record — ``None`` for a runner composed over no file entry."""
        ...

    def capability_snapshot(self) -> tuple[HarnessCapability, ...]: ...


@dataclass(frozen=True)
class Registration:
    """Register (doubling as the runner-level liveness heartbeat), record the identity the reply
    names, then mirror the hub's pause brake locally."""

    ctx: RegistrationContext

    def run(self) -> None:
        ctx = self.ctx
        workspace_id = ctx.workspaces.home(ctx.config.bound_workspace_ids)
        provider = ctx.workspaces.provider(workspace_id)
        pause = ctx.stores.pause
        declining = declining_report(
            ThrottleGate(ctx).claims(RunnerBrakes(local=pause.local_paused(), hub=pause.hub_paused())),
            now=ctx.clock.now(),
        )
        try:
            reply = ctx.hub.register_runner(
                ctx.config.runner_name,
                workspace_id,
                env_capacity=provider.capacity() if provider is not None else None,
                url=ctx.config.public_url or None,
                redirect_uris=ctx.config.redirect_uris,
                capabilities=ctx.capability_snapshot(),
                # A registered host reports the roster itself, so its runners send none.
                subscriptions=(
                    tuple(
                        SubscriptionDeclaration(slug=s.slug, name=s.name, provider=s.provider)
                        for s in ctx.subscriptions
                    )
                    if ctx.config.declares_subscriptions
                    else None
                ),
                gates=ctx.config.gates,
                projects=ctx.config.projects,
                bindings=ctx.config.bindings,
                agents=ctx.config.agents or None,
                subscription=ctx.config.subscription,
                declining=tuple(DecliningDeclaration(d.lever, d.detail, d.subscription, d.until) for d in declining),
            )
        except RunnerVersionRefused as refused:
            was = ctx.identity_verdict.refuse_version(refused.refusal.detail)
            log_transition(ctx.identity_verdict, was)
            return
        except HubClientError:
            return  # hub unreachable — the last identity and the last-mirrored brake hold
        ctx.hub_version.hold(reply)
        if ctx.identity_verdict.version_refused():
            was = ctx.identity_verdict.admit_version()
            log_transition(ctx.identity_verdict, was)
        now = ctx.clock.now()
        stored = ctx.stores.identity.runner_identity()
        state = judged(ctx.config.runner_id, reply.runner_id)
        if state is IdentityState.MISMATCHED:
            was = ctx.identity_verdict.record(
                state,
                store_runner_id=ctx.config.runner_id,
                token_runner_id=reply.runner_id,
                checked_at=now,
            )
            log_transition(ctx.identity_verdict, was)
            return  # the reply names another runner: its brake is not this runner's
        tenant_id, tenant_name = answered_tenant(stored, reply.tenant_id, reply.tenant_name)
        identity = RunnerIdentity(
            runner_id=reply.runner_id,
            runner_name=reply.runner_name,
            registered_at=now,
            tenant_id=tenant_id,
            tenant_name=tenant_name,
        )
        # The row first, then the holder: a crash between them leaves the holder to be rebuilt from the row.
        ctx.stores.identity.record_runner_identity(identity)
        ctx.identity.hold(identity)
        if reply.projects is not None:
            ctx.stores.served_projects.record_served_projects(reply.projects, at=now)
        if stored is None or (stored.runner_name, stored.tenant_name) != (identity.runner_name, identity.tenant_name):
            _log.info(
                "runner identity recorded",
                runner_id=identity.runner_id,
                runner_name=identity.runner_name,
                tenant_id=identity.tenant_id,
                tenant_name=identity.tenant_name,
                was_runner_name=stored.runner_name if stored is not None else None,
            )
        if not ctx.identity_verdict.matched():
            was = ctx.identity_verdict.record(
                state,
                store_runner_id=identity.runner_id,
                token_runner_id=reply.runner_id,
                checked_at=now,
            )
            log_transition(ctx.identity_verdict, was)
        self._seed_settings()
        try:
            paused = ctx.hub.fetch_runner_paused(reply.runner_id)
        except HubClientError:
            return  # keep the last-mirrored brake
        ctx.stores.pause.set_hub_paused(paused=paused, at=ctx.clock.now())

    def _seed_settings(self) -> None:
        seed = self.ctx.settings_seed
        if seed is None:
            return
        try:
            seed.run()
        except (SettingsInvalid, ValueError) as exc:
            _log.warning("runner settings seed refused", runner_id=self.ctx.config.runner_id, detail=str(exc))
