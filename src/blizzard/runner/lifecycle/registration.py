"""The registration step — register at the hub, keep the identity its reply names, mirror the hub's
pause brake.

The hub resolves who this runner is from its bearer token; the runner only declares its configured
name. The store's identity row keeps the id of the runner the store belongs to: a registration adopts
the reply's id only into a store that names none, and otherwise refreshes the name and the tenant
the reply names (a reply naming none, from a hub that predates tenants, keeps the row's). A reply
naming another runner — a token rotated since the tick's identity check, or a hub with no identity
read — turns the verdict to a mismatch and mirrors no brake. A failed registration leaves the row,
the holder and the mirrored brake as they stood."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.capability_snapshot import HarnessCapability
from blizzard.runner.hub.client import HubClientError, IHubClient, SubscriptionDeclaration
from blizzard.runner.hub.held_token import HubTokenHolder
from blizzard.runner.hub.identity import (
    ICurrentRunnerIdentity,
    IdentityState,
    IdentityVerdictHolder,
    IWriteRunnerIdentityRepository,
    RunnerIdentity,
    RunnerIdentityHolder,
    answered_tenant,
)
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
    def pause(self) -> IWritePauseRepository: ...


class RegistrationConfig(Protocol):
    @property
    def runner_name(self) -> str: ...
    @property
    def workspace_id(self) -> str: ...
    @property
    def env_capacity(self) -> int | None: ...
    @property
    def public_url(self) -> str: ...
    @property
    def redirect_uris(self) -> tuple[str, ...]: ...
    @property
    def gates(self) -> tuple[str, ...]: ...


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
    def config(self) -> RegistrationConfig: ...
    @property
    def subscriptions(self) -> Sequence[DeclaredSubscription]: ...
    def capability_snapshot(self) -> tuple[HarnessCapability, ...]: ...


@dataclass(frozen=True)
class Registration:
    """Register (doubling as the runner-level liveness heartbeat), record the identity the reply
    names, then mirror the hub's pause brake locally."""

    ctx: RegistrationContext

    def run(self) -> None:
        ctx = self.ctx
        try:
            reply = ctx.hub.register_runner(
                ctx.config.runner_name,
                ctx.config.workspace_id,
                env_capacity=ctx.config.env_capacity,
                url=ctx.config.public_url or None,
                redirect_uris=ctx.config.redirect_uris,
                capabilities=ctx.capability_snapshot(),
                subscriptions=tuple(
                    SubscriptionDeclaration(slug=s.slug, name=s.name, provider=s.provider) for s in ctx.subscriptions
                ),
                gates=ctx.config.gates,
            )
        except HubClientError:
            return  # hub unreachable — the last identity and the last-mirrored brake hold
        now = ctx.clock.now()
        stored = ctx.stores.identity.runner_identity()
        if stored is not None and stored.runner_id != reply.runner_id:
            was = ctx.identity_verdict.record(
                IdentityState.MISMATCHED,
                store_runner_id=stored.runner_id,
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
