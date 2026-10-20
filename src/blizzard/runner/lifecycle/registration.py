"""The registration step — register at the hub, keep the identity and the served projects its reply
names, mirror the hub's pause brake.

The hub resolves who this runner is from its bearer token; the runner declares its configured name and
the bindings and subscription its host file gives it. The runner's id is configured and its store is bound to
it, so a registration never adopts one: it records the reply's name and tenant against the configured id (a
reply naming no tenant, from a hub that predates tenants, keeps the row's). A reply naming another runner — a
token rotated since the tick's identity check, or a hub with no identity read — turns the verdict to a mismatch
and mirrors no brake. The registration declares the configured projects, and a reply answering them replaces the
stored answer whole once the identity row is recorded; a reply answering none, from a hub that predates
projects, keeps it. A failed registration leaves the row, the answer, the holder and the mirrored brake as they
stood. One the hub's window refuses over the runner's version halts the runner alone through its identity verdict,
and the next admitted registration lifts it; each reply's hub version and window are held for the status."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.runner.environments.workspaces import IWorkspaces
from blizzard.runner.harness.capability_snapshot import HarnessCapability
from blizzard.runner.hub.client import (
    BindingDeclaration,
    HubClientError,
    IHubClient,
    RunnerVersionRefused,
    SubscriptionDeclaration,
)
from blizzard.runner.hub.held_token import HubTokenHolder
from blizzard.runner.hub.hub_version import HubVersionHolder
from blizzard.runner.hub.identity import (
    ICurrentRunnerIdentity,
    IdentityVerdictHolder,
    IWriteRunnerIdentityRepository,
    RunnerIdentity,
    RunnerIdentityHolder,
    answered_tenant,
)
from blizzard.runner.hub.served_projects import IWriteServedProjectsRepository
from blizzard.runner.lifecycle.identity_check import log_transition
from blizzard.runner.throttle.pause import IWritePauseRepository

_log = get_logger("blizzard.runner.loop")


def registered_runner_id(identity: ICurrentRunnerIdentity) -> str | None:
    """This runner's hub-minted id, or ``None`` before its first successful registration — when
    nothing may be claimed and no route may be judged ours or another runner's."""
    current = identity.current()
    return current.runner_id if current is not None else None


class DeclaredSubscription(Protocol):
    """One subscription the registration declares to the hub."""

    @property
    def slug(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def provider(self) -> str: ...


class RegistrationStores(Protocol):
    @property
    def identity(self) -> IWriteRunnerIdentityRepository: ...
    @property
    def served_projects(self) -> IWriteServedProjectsRepository: ...
    @property
    def pause(self) -> IWritePauseRepository: ...


class RegistrationConfig(Protocol):
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
    def subscription(self) -> str | None: ...
    @property
    def declares_subscriptions(self) -> bool: ...


class RegistrationContext(Protocol):
    """What the registration step reads and writes."""

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
    def hub_token(self) -> HubTokenHolder: ...
    @property
    def hub_version(self) -> HubVersionHolder: ...
    @property
    def config(self) -> RegistrationConfig: ...
    @property
    def subscriptions(self) -> Sequence[DeclaredSubscription]: ...
    @property
    def workspaces(self) -> IWorkspaces: ...
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
                subscription=ctx.config.subscription,
            )
        except RunnerVersionRefused as refused:
            was = ctx.identity_verdict.refuse_version(refused.refusal.detail)
            log_transition(ctx.identity_verdict, was, token_fixed=ctx.hub_token.fixed())
            return  # the window refuses this runner: it halts alone until a registration is admitted
        except HubClientError:
            return  # hub unreachable — the last identity and the last-mirrored brake hold
        ctx.hub_version.hold(reply)
        if ctx.identity_verdict.version_refused():
            was = ctx.identity_verdict.admit_version()
            log_transition(ctx.identity_verdict, was, token_fixed=ctx.hub_token.fixed())
        now = ctx.clock.now()
        stored = ctx.stores.identity.runner_identity()
        if reply.runner_id != ctx.config.runner_id:
            was = ctx.identity_verdict.record(
                IdentityState.MISMATCHED,
                store_runner_id=ctx.config.runner_id,
                token_runner_id=reply.runner_id,
                checked_at=now,
            )
            log_transition(ctx.identity_verdict, was, token_fixed=ctx.hub_token.fixed())
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
                IdentityState.MATCHED,
                store_runner_id=identity.runner_id,
                token_runner_id=reply.runner_id,
                checked_at=now,
            )
            log_transition(ctx.identity_verdict, was, token_fixed=ctx.hub_token.fixed())
        try:
            paused = ctx.hub.fetch_runner_paused(reply.runner_id)
        except HubClientError:
            return  # keep the last-mirrored brake
        ctx.stores.pause.set_hub_paused(paused=paused, at=ctx.clock.now())
