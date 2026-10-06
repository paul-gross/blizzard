"""The registration step — register at the hub, keep the identity its reply names, mirror the hub's
pause brake.

The hub resolves who this runner is from its bearer token; the runner only declares its configured
name. Every successful registration replaces the store's identity row and the process's holder with
the reply's id and name; a failed one leaves the row, the holder and the mirrored brake as they stood."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.capability_snapshot import HarnessCapability
from blizzard.runner.hub.client import HubClientError, IHubClient, SubscriptionDeclaration
from blizzard.runner.hub.identity import (
    ICurrentRunnerIdentity,
    IWriteRunnerIdentityRepository,
    RunnerIdentity,
    RunnerIdentityHolder,
)
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
        identity = RunnerIdentity(
            runner_id=reply.runner_id, runner_name=reply.runner_name, registered_at=ctx.clock.now()
        )
        held = ctx.identity.current()
        # The row first, then the holder: a crash between them leaves the holder to be rebuilt from the row.
        ctx.stores.identity.record_runner_identity(identity)
        ctx.identity.hold(identity)
        if held is None or (held.runner_id, held.runner_name) != (identity.runner_id, identity.runner_name):
            _log.info(
                "runner identity changed",
                runner_id=identity.runner_id,
                runner_name=identity.runner_name,
                was_runner_id=held.runner_id if held is not None else None,
                was_runner_name=held.runner_name if held is not None else None,
            )
        try:
            paused = ctx.hub.fetch_runner_paused(reply.runner_id)
        except HubClientError:
            return  # keep the last-mirrored brake
        ctx.stores.pause.set_hub_paused(paused=paused, at=ctx.clock.now())
