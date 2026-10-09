"""The identity check — the head of every tick, before any other hub call.

The runner's store belongs to the runner that wrote it. Each tick asks the hub which runner the
bearer token belongs to and compares that id with the one the store's identity row holds — the id
alone, since a runner never changes tenant and ids are unique across the hub. A store that names no
runner adopts the token's. A different runner means a wrong token or a hub URL pointing somewhere
else: the runner refuses to drive its leases or deliver its buffered facts, and checks again next
tick, so restoring the right token or hub resumes it with nothing lost. A matching answer also
fills in the tenant it names when the row lacks it or holds another name for it — the first
contact after an upgrade gives a row written before tenants its tenant. While refusing, each check
first re-reads the token from the runtime dir's ``.env`` and checks with a corrected one, so restoring
the token there needs no restart; a token the process environment supplied stays fixed until one. A
hub that does not answer, or refuses the token, leaves the verdict as it stood: an outage never reads
as a mismatch."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.runner.hub.client import HubClientError, IdentityRouteMissing, ITokenIdentityReader, TokenRefusal
from blizzard.runner.hub.held_token import HubTokenHolder, TokenReread
from blizzard.runner.hub.identity import (
    IdentityState,
    IdentityVerdictHolder,
    IWriteRunnerIdentityRepository,
    RunnerIdentity,
    RunnerIdentityHolder,
    answered_tenant,
)

_log = get_logger("blizzard.runner.loop")


class IdentityCheckStores(Protocol):
    @property
    def identity(self) -> IWriteRunnerIdentityRepository: ...


class IdentityCheckContext(Protocol):
    """What the identity check reads and writes."""

    @property
    def stores(self) -> IdentityCheckStores: ...
    @property
    def clock(self) -> IClock: ...
    @property
    def token_identity(self) -> ITokenIdentityReader: ...
    @property
    def identity(self) -> RunnerIdentityHolder: ...
    @property
    def identity_verdict(self) -> IdentityVerdictHolder: ...
    @property
    def hub_token(self) -> HubTokenHolder: ...


def log_transition(holder: IdentityVerdictHolder, was: IdentityState, *, token_fixed: bool) -> None:
    """Log a verdict that changed state, once per change, naming both ids and the reset command — and,
    when ``token_fixed``, that only a restart takes up a corrected token."""
    verdict = holder.verdict()
    if verdict.state is was:
        return
    if verdict.state is IdentityState.MISMATCHED:
        _log.warning(
            "runner refuses to work: its token belongs to another runner than its store",
            store_runner_id=verdict.store_runner_id,
            token_runner_id=verdict.token_runner_id,
            reset_command=verdict.reset_command,
        )
        if token_fixed:
            _log.warning("runner token comes from the process environment: restart the runner to change it")
    elif verdict.state is IdentityState.MATCHED:
        _log.info(
            "runner identity verified",
            runner_id=verdict.token_runner_id,
            was=str(was),
        )


@dataclass(frozen=True)
class IdentityCheck:
    """Verify that the token resolves to the store's runner, adopting it when the store names none."""

    ctx: IdentityCheckContext

    def run(self) -> None:
        ctx = self.ctx
        if ctx.identity_verdict.verdict().state is IdentityState.MISMATCHED:
            _take_up_a_corrected_token(ctx.hub_token)
        try:
            answer = ctx.token_identity.identity()
        except IdentityRouteMissing:
            ctx.identity_verdict.check_by_registration()
            return
        except HubClientError:
            return  # unreachable — the verdict stands
        if isinstance(answer, TokenRefusal):
            _log.info("hub refused the runner's token", reason=str(answer.reason))
            return
        now = ctx.clock.now()
        stored = ctx.stores.identity.runner_identity()
        tenant_id, tenant_name = answered_tenant(stored, answer.tenant_id, answer.tenant_name)
        if stored is None:
            adopted = RunnerIdentity(
                runner_id=answer.runner_id,
                runner_name=answer.runner_name,
                registered_at=now,
                tenant_id=tenant_id,
                tenant_name=tenant_name,
            )
            _record(ctx, adopted)
            stored = adopted
        elif stored.runner_id == answer.runner_id:
            refreshed = replace(stored, tenant_id=tenant_id, tenant_name=tenant_name)
            if refreshed != stored:
                _record(ctx, refreshed)
                stored = refreshed
        state = IdentityState.MATCHED if stored.runner_id == answer.runner_id else IdentityState.MISMATCHED
        was = ctx.identity_verdict.record(
            state, store_runner_id=stored.runner_id, token_runner_id=answer.runner_id, checked_at=now
        )
        log_transition(ctx.identity_verdict, was, token_fixed=ctx.hub_token.fixed())


def _record(ctx: IdentityCheckContext, identity: RunnerIdentity) -> None:
    # The row first, then the holder: a crash between them leaves the holder to be rebuilt from the row.
    ctx.stores.identity.record_runner_identity(identity)
    ctx.identity.hold(identity)


def _take_up_a_corrected_token(token: HubTokenHolder) -> None:
    """Re-read the token from ``.env`` so the check below runs with a corrected one."""
    reread = token.reread()
    if reread is TokenReread.SWITCHED:
        _log.info("runner token changed in its .env: checking its identity with the new one")
    elif reread is TokenReread.UNREADABLE:
        _log.debug("runner token file unreadable: checking its identity with the token in use")
