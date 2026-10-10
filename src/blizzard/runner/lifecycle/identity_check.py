"""The identity check — the head of every tick, before any other hub call.

The runner's store belongs to the runner that wrote it. Each tick asks the hub which runner the bearer token
belongs to and compares that id with the one the store's identity row holds — the id alone, since a runner
never changes tenant and ids are unique across the hub. A store that names no runner yet matches any token: it
is the first registration, not the check, that adopts the id its reply names, so the process's identity still
names only a runner that has registered. A different runner means a wrong token or a hub URL pointing
somewhere else: the runner refuses to drive its leases or deliver its buffered facts, and checks again next
tick, so restoring the right token or hub resumes it with nothing lost. A matching answer also fills in the
tenant it names when the row lacks it or holds another name for it — the first contact after an upgrade gives
a row written before tenants its tenant. While refusing, each check first re-reads the token from the runtime
dir's ``.env`` and checks with a corrected one, so restoring the token there needs no restart; a token the
process environment supplied stays fixed until one. A hub that does not answer, or refuses the token, leaves
the verdict as it stood: an outage never reads as a mismatch.

A hub forward served outside the loop — the hub proxy — is a first contact too: while the loop has not
checked yet, it judges the hub's answer the same way first (:class:`ProxyIdentityCheck`), reading the
store and writing nothing."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.runner.hub.client import (
    HubClientError,
    IdentityRouteMissing,
    ITokenIdentityReader,
    TokenIdentity,
    TokenRefusal,
)
from blizzard.runner.hub.held_token import HubTokenHolder, TokenReread
from blizzard.runner.hub.identity import (
    ICurrentIdentityVerdict,
    IdentityState,
    IdentityVerdict,
    IdentityVerdictHolder,
    IReadRunnerIdentityRepository,
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


def judged(stored: RunnerIdentity | None, token_runner_id: str) -> IdentityState:
    """The verdict on a token the hub resolved to ``token_runner_id``: matched when the store names that
    runner, or names none yet — its first registration adopts the id — and mismatched otherwise."""
    if stored is None or stored.runner_id == token_runner_id:
        return IdentityState.MATCHED
    return IdentityState.MISMATCHED


def check_token(
    reader: ITokenIdentityReader,
    rows: IReadRunnerIdentityRepository,
    verdict: IdentityVerdictHolder,
    now: datetime,
    *,
    token_fixed: bool,
) -> TokenIdentity | None:
    """Ask the hub which runner the token belongs to, judge it against the store's identity row, and hold
    the verdict. Returns the hub's answer; ``None`` when the hub refused the token or has no identity read
    — the latter left to the registration reply. Raises :class:`HubClientError` when the hub does not
    answer. Either way short of an answer, the verdict stands."""
    try:
        answer = reader.identity()
    except IdentityRouteMissing:
        verdict.check_by_registration()
        return None
    if isinstance(answer, TokenRefusal):
        _log.info("hub refused the runner's token", reason=str(answer.reason))
        return None
    stored = rows.runner_identity()
    was = verdict.record(
        judged(stored, answer.runner_id),
        store_runner_id=stored.runner_id if stored is not None else None,
        token_runner_id=answer.runner_id,
        checked_at=now,
    )
    log_transition(verdict, was, token_fixed=token_fixed)
    return answer


@dataclass(frozen=True)
class IdentityCheck:
    """Verify that the token resolves to the store's runner, filling in the tenant a matching answer names."""

    ctx: IdentityCheckContext

    def run(self) -> None:
        ctx = self.ctx
        if ctx.identity_verdict.verdict().state is IdentityState.MISMATCHED:
            _take_up_a_corrected_token(ctx.hub_token)
        try:
            answer = check_token(
                ctx.token_identity,
                ctx.stores.identity,
                ctx.identity_verdict,
                ctx.clock.now(),
                token_fixed=ctx.hub_token.fixed(),
            )
        except HubClientError:
            return  # unreachable — the verdict stands
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
    answer against the store's row. A match is remembered for the process, so the forward asks once; a
    refused token, a hub with no identity read, or a mismatch forwards nothing and is asked again next
    time. It writes nothing the status reports: the verdict stays the loop's, which alone logs a
    transition and re-reads a corrected token, and prevails once it has checked."""

    def __init__(self, rows: IReadRunnerIdentityRepository, verdict: ICurrentIdentityVerdict, clock: IClock) -> None:
        self._rows = rows
        self._verdict = verdict
        self._clock = clock
        self._lock = threading.Lock()
        self._matched: IdentityVerdict | None = None

    def verdict_on_contact(self, ask: Callable[[], str | None]) -> IdentityVerdict:
        current = self._verdict.verdict()
        if current.state is not IdentityState.UNCHECKED or self._verdict.registration_checks():
            return current
        with self._lock:
            if self._matched is not None:
                return self._matched
        token_runner_id = ask()
        if token_runner_id is None:
            return current
        stored = self._rows.runner_identity()
        judged_now = IdentityVerdict(
            state=judged(stored, token_runner_id),
            store_runner_id=stored.runner_id if stored is not None else None,
            token_runner_id=token_runner_id,
            checked_at=self._clock.now(),
            reset_command=current.reset_command,
        )
        if judged_now.state is IdentityState.MATCHED:
            with self._lock:
                self._matched = judged_now
        return judged_now


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
