"""The identity check — the head of every tick, before any other hub call.

A runner is the one its host file's ``[[runner]] id`` names, and its store is bound to that id before anything runs.
Each tick asks the hub which runner the bearer token belongs to and compares that id with the configured one — the id
alone, since a runner never changes tenant and ids are unique across the hub. The store's identity row takes no part:
configuration, not the store, says whose it is. A different runner means a wrong token or a hub URL pointing
somewhere else, and a token the hub refuses — unknown, revoked, retired — is a refusal of its own: either way the
runner refuses to drive its leases or deliver its buffered facts, and checks again next
tick, so restoring the right token or hub resumes it with nothing lost. A matching answer also fills in the
tenant it names when the row lacks it or holds another name for it — the first contact after an upgrade gives
a row written before tenants its tenant. While refusing, each check first re-reads the token from the host
dir's ``.env`` and checks with a corrected one, so restoring the token there needs no restart; a token the
process environment or the host file supplied stays fixed until a restart. A hub that does not answer leaves the
verdict as it stood: an outage never reads as a mismatch. The check writes only the token's own verdict; the
runner's standing under a registered host is the host tick's, and neither clears the other.

A matching answer also carries the hub's ``fleet`` flags, which the check applies to the runner's flags before any
later step of the tick reads them. Any other outcome — an outage, a refused token, a mismatch, a hub with no identity
read — leaves the flags as they stood: a mismatched answer is not this runner's hub's, and an outage never flips
behavior.

A hub forward served outside the loop — the hub proxy — is a first contact too: while the loop has not
checked yet, it judges the hub's answer the same way first (:class:`ProxyIdentityCheck`), writing nothing."""

from __future__ import annotations

import threading
from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.runner.hub.client import (
    HubClientError,
    IdentityRouteMissing,
    ITokenIdentityReader,
    TokenIdentity,
    TokenRefusal,
)
from blizzard.runner.hub.flags import FlagsHolder
from blizzard.runner.hub.held_token import HubTokenHolder, TokenReread
from blizzard.runner.hub.identity import (
    ICurrentIdentityVerdict,
    IdentityVerdict,
    IdentityVerdictHolder,
    IWriteRunnerIdentityRepository,
    RunnerIdentity,
    RunnerIdentityHolder,
    answered_tenant,
    judged,
)

_log = get_logger("blizzard.runner.loop")


class IdentityCheckStores(Protocol):
    @property
    def identity(self) -> IWriteRunnerIdentityRepository: ...


class IdentityCheckConfig(Protocol):
    @property
    def runner_id(self) -> str: ...


class IdentityCheckContext(Protocol):
    """What the identity check reads and writes."""

    @property
    def config(self) -> IdentityCheckConfig: ...
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
    @property
    def flags(self) -> FlagsHolder: ...


def log_transition(holder: IdentityVerdictHolder, was: IdentityState, *, token_fixed: bool) -> None:
    """Log a verdict that changed state, once per change, naming both ids and the remedy — and,
    when ``token_fixed``, that only a restart takes up a corrected token."""
    verdict = holder.verdict()
    if verdict.state is was:
        return
    if verdict.state is IdentityState.MISMATCHED:
        _log.warning(
            "runner refuses to work: its token belongs to another runner than the configured one",
            store_runner_id=verdict.store_runner_id,
            token_runner_id=verdict.token_runner_id,
            reset_command=verdict.reset_command,
        )
    elif verdict.state is IdentityState.REFUSED:
        _log.warning(
            "runner refuses to work: the hub refuses its token",
            store_runner_id=verdict.store_runner_id,
            reason=verdict.reason,
            reset_command=verdict.reset_command,
        )
    elif verdict.state is IdentityState.VERSION_REFUSED:
        _log.warning("runner refuses to work: the hub's version window refuses it", reason=verdict.reason)
        return
    elif verdict.state is IdentityState.MATCHED:
        _log.info(
            "runner identity verified",
            runner_id=verdict.token_runner_id,
            was=str(was),
        )
        return
    else:
        return
    if token_fixed:
        _log.warning(
            "runner token comes from the process environment or the host file: restart the runner to change it"
        )


class ForeignStoreKind(StrEnum):
    """How a store naming a runner none of ours is foreign. ``MIXED`` also names one of ours, so it is the wrong
    file and a reset would drop the history of the runner it holds; ``ALIEN`` names only foreign runners, so the
    id configured over it, or the token held for it, is the wrong one."""

    MIXED = "mixed"
    ALIEN = "alien"


@domain_model
@dataclass(frozen=True)
class ForeignStore:
    """A store that is not ours: how (``kind``), every runner its identity rows ``named``, and the ``foreign``
    ones among them, in the store's order."""

    kind: ForeignStoreKind
    named: tuple[str, ...]
    foreign: tuple[str, ...]


def foreign_store(named: tuple[str, ...], ours: Collection[str]) -> ForeignStore | None:
    """Whether a store whose identity rows name ``named`` holds a runner none of ``ours`` is, or ``None`` when it
    names only ours or none has joined it yet. A runner started over a foreign store would orphan the other's leases
    and backlog, so it is refused untouched."""
    foreign = tuple(runner for runner in named if runner not in ours)
    if not foreign:
        return None
    kind = ForeignStoreKind.MIXED if len(foreign) < len(named) else ForeignStoreKind.ALIEN
    return ForeignStore(kind=kind, named=named, foreign=foreign)


def reset_hazard(reset: str, runner_id: str) -> str:
    """What ``reset`` costs: it starts ``runner_id`` on a fresh store, dropping every fact below the hub's
    high-water mark."""
    return (
        f"`{reset}` sets the store aside and starts {runner_id} on a fresh one, dropping every fact below the hub's "
        "high-water mark, so use it only for a runner with no history"
    )


def foreign_store_reason(
    named: tuple[str, ...], configured_id: str, reset: str, *, configured: tuple[str, ...] = ()
) -> str | None:
    """Why a store whose identity rows name ``named`` is not ``configured_id``'s, or ``None`` when it names only
    runners this host configures — ``configured_id`` and the other ``configured`` ids of its ``[[runner]]``
    entries. A mixed store is the wrong file, so its remedy is to restore the right store, never ``reset``; an
    alien one has a wrong configured id, so its remedy is to correct the id, ``reset`` named there with its
    hazard."""
    verdict = foreign_store(named, {configured_id, *configured})
    if verdict is None:
        return None
    if verdict.kind is ForeignStoreKind.MIXED:
        return (
            f"the store names runners {', '.join(named)}, and {', '.join(verdict.foreign)} is configured by no "
            f"[[runner]] of this host; it cannot be told apart from {configured_id}'s, so it is left untouched — "
            "stop and restore the right store"
        )
    return (
        f"the store belongs to runner {', '.join(named)}, not the configured {configured_id}; correct the `id` in "
        f"the host file to {named[0]}. {reset_hazard(reset, configured_id)}"
    )


def check_token(
    reader: ITokenIdentityReader,
    configured_id: str,
    verdict: IdentityVerdictHolder,
    now: datetime,
    *,
    token_fixed: bool,
) -> TokenIdentity | None:
    """Ask the hub which runner the token belongs to, judge it against the configured runner, and hold
    the verdict. Returns the hub's answer; ``None`` when the hub refused the token — held as a refusal of its
    own, re-checked each tick — or has no identity read, left to the registration reply. Raises
    :class:`HubClientError` when the hub does not answer, and the verdict stands."""
    try:
        answer = reader.identity()
    except IdentityRouteMissing:
        verdict.check_by_registration()
        return None
    if isinstance(answer, TokenRefusal):
        was = verdict.refuse(
            store_runner_id=configured_id,
            reason=_refused(answer),
            checked_at=now,
        )
        log_transition(verdict, was, token_fixed=token_fixed)
        return None
    was = verdict.record(
        judged(configured_id, answer.runner_id),
        store_runner_id=configured_id,
        token_runner_id=answer.runner_id,
        checked_at=now,
    )
    log_transition(verdict, was, token_fixed=token_fixed)
    return answer


class TickScope(StrEnum):
    """How much of a tick runs after the identity check; the local steps run in every scope."""

    RUN_ALL = "run_all"
    REGISTRATION_ONLY = "registration_only"
    SKIP = "skip"


def tick_scope(verdict: IdentityVerdict, *, registration_checks: bool) -> TickScope:
    """A refusing verdict drives no lease and delivers nothing; the registration still runs where only its reply can
    lift the refusal — a hub with no identity read (``registration_checks``), or a refused version."""
    if not verdict.refusing():
        return TickScope.RUN_ALL
    if registration_checks or verdict.state is IdentityState.VERSION_REFUSED:
        return TickScope.REGISTRATION_ONLY
    return TickScope.SKIP


@dataclass(frozen=True)
class IdentityCheck:
    """Verify that the token resolves to the configured runner, filling in the tenant a matching answer names and
    applying the hub's ``fleet`` flags."""

    ctx: IdentityCheckContext

    def run(self) -> None:
        ctx = self.ctx
        if ctx.identity_verdict.token_refusing():
            _take_up_a_corrected_token(ctx.hub_token)
        try:
            answer = check_token(
                ctx.token_identity,
                ctx.config.runner_id,
                ctx.identity_verdict,
                ctx.clock.now(),
                token_fixed=ctx.hub_token.fixed(),
            )
        except HubClientError:
            return  # unreachable — the verdict stands
        if answer is not None and ctx.identity_verdict.matched():
            ctx.flags.apply_fleet_answer(answer.fleet_flags)
        stored = ctx.stores.identity.runner_identity()
        if answer is None or stored is None or stored.runner_id != answer.runner_id:
            return
        tenant_id, tenant_name = answered_tenant(stored, answer.tenant_id, answer.tenant_name)
        refreshed = replace(stored, tenant_id=tenant_id, tenant_name=tenant_name)
        if refreshed != stored:
            _record(ctx, refreshed)


class ProxyIdentityCheck:
    """The identity check a hub forward outside the loop runs as its process's first contact. While the
    loop has not checked, the forward asks the hub which runner its token belongs to and this judges the
    answer against the configured runner. A match is remembered for the process, so the forward asks once; a
    refused token, a hub with no identity read, or a mismatch forwards nothing and is asked again next
    time. It writes nothing the status reports: the verdict stays the loop's, which alone logs a
    transition and re-reads a corrected token, and prevails once it has checked."""

    def __init__(self, configured_id: str, verdict: ICurrentIdentityVerdict, clock: IClock) -> None:
        self._configured_id = configured_id
        self._verdict = verdict
        self._clock = clock
        self._lock = threading.Lock()
        self._matched: IdentityVerdict | None = None

    def verdict_on_contact(self, reader: ITokenIdentityReader) -> IdentityVerdict:
        current = self._verdict.verdict()
        if current.state is not IdentityState.UNCHECKED or self._verdict.registration_checks():
            return current
        with self._lock:
            if self._matched is not None:
                return self._matched
        try:
            answer = reader.identity()
        except IdentityRouteMissing:
            return current
        if isinstance(answer, TokenRefusal):
            return replace(
                current,
                state=IdentityState.REFUSED,
                store_runner_id=self._configured_id,
                checked_at=self._clock.now(),
                reason=_refused(answer),
            )
        judged_now = replace(
            current,
            state=judged(self._configured_id, answer.runner_id),
            store_runner_id=self._configured_id,
            token_runner_id=answer.runner_id,
            checked_at=self._clock.now(),
        )
        if judged_now.state is IdentityState.MATCHED:
            with self._lock:
                self._matched = judged_now
        return judged_now


def _refused(refusal: TokenRefusal) -> str:
    return f"the hub refuses this runner's token ({refusal.reason})"


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
