"""Each forge key's rate budget — one per (forge, secret) pair, held per tenant beside the forge registry.

A budget learns from the ``X-RateLimit-*`` headers the forge answers with, and turns its least important callers away
before a request is made once the remaining budget falls: ``landing`` while any remains, ``watching`` above a fifth of
the limit, ``sweeping`` above two fifths. A budget with no live reading — never heard from, answered without rate
headers, past its reset, or just restarted — admits every caller; one marked spent by a forge refusal refuses every
caller until that refusal lifts. The rule for whether an answer is a rate refusal and when it lifts lives here too, free
of any HTTP library, so every adapter that speaks to a forge reads it the same way."""

from __future__ import annotations

# Guards each budget's own reading, never a store write, and never held across a request.
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.hub.forge.landing import ForgeRateLimited

#: The bucket every seam call is admitted against, and the one a reading lands in when the answer names none.
CORE_BUCKET = "core"
#: A rate limit that names no usable instant, and the floor of a secondary limit.
DEFAULT_RESUME = timedelta(seconds=60)


class ForgePriority(StrEnum):
    """How important a forge caller is; the less important, the earlier a falling budget turns it away."""

    LANDING = "landing"
    WATCHING = "watching"
    SWEEPING = "sweeping"


class BudgetNoticeKind(StrEnum):
    BUDGET_LOW = "forge-budget-low"
    RATE_LIMITED = "forge-rate-limited"


@domain_model
@dataclass(frozen=True)
class BudgetKey:
    """One budget's key: a forge and the secret its calls go out under; ``None`` is the forge's anonymous budget."""

    forge_id: str
    secret_name: str | None


@domain_model
@dataclass(frozen=True)
class BudgetReading:
    """What the forge last said of one bucket: its limit, what remains of it, and when it resets."""

    bucket: str
    limit: int
    remaining: int
    reset: datetime


@domain_model
@dataclass(frozen=True)
class BudgetNotice:
    """A budget's once-per-window notice: turned a caller away (``BUDGET_LOW``) or refused by the forge
    (``RATE_LIMITED``), with the last reading's remaining and limit when there is one."""

    kind: BudgetNoticeKind
    key: BudgetKey
    bucket: str
    remaining: int | None
    limit: int | None
    reset: datetime


class ForgeBudgetLow(ForgeRateLimited):
    """A forge call turned away before it was sent: its budget is too low for its caller's priority."""


def _admits(priority: ForgePriority, reading: BudgetReading) -> bool:
    match priority:
        case ForgePriority.LANDING:
            return reading.remaining > 0
        case ForgePriority.WATCHING:
            return reading.remaining * 5 > reading.limit
        case ForgePriority.SWEEPING:
            return reading.remaining * 5 > 2 * reading.limit


class _Budget:
    """One key's readings by bucket, its spent-until, and the window each notice kind was last queued for."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.readings: dict[str, BudgetReading] = {}
        self.spent_until: datetime | None = None
        self.noticed: dict[BudgetNoticeKind, datetime] = {}


class ForgeBudgets:
    """Every budget of one tenant, each made on its key's first use. It outlives every adapter, so an edit that
    rebuilds one keeps the budget its calls spend."""

    def __init__(self, *, clock: IClock) -> None:
        self._clock = clock
        self._budgets: dict[BudgetKey, _Budget] = {}
        self._notices: list[BudgetNotice] = []
        self._guard = threading.Lock()

    def admit(self, key: BudgetKey, priority: ForgePriority, bucket: str = CORE_BUCKET) -> None:
        """Return when ``key``'s budget admits a ``priority`` caller to ``bucket``; :class:`ForgeBudgetLow`, resuming
        at the reading's reset or the spent-until, when it does not."""
        budget = self._budget(key)
        now = self._clock.now()
        with budget.lock:
            spent_until = budget.spent_until
            if spent_until is not None and now < spent_until:
                self._notice(budget, BudgetNoticeKind.BUDGET_LOW, key, bucket, budget.readings.get(bucket), spent_until)
                raise ForgeBudgetLow(spent_until, f"{key.forge_id}: spent until {spent_until}")
            reading = budget.readings.get(bucket)
            if reading is None or reading.reset <= now or _admits(priority, reading):
                return
            self._notice(budget, BudgetNoticeKind.BUDGET_LOW, key, bucket, reading, reading.reset)
        raise ForgeBudgetLow(
            reading.reset, f"{key.forge_id}: {reading.remaining}/{reading.limit} left is too low for {priority}"
        )

    def learn(self, key: BudgetKey, headers: Mapping[str, str], bucket: str = CORE_BUCKET) -> None:
        """Take the reading an answer's rate headers carry into the bucket ``X-RateLimit-Resource`` names, or into
        ``bucket`` when it names none; an answer without a whole reading teaches nothing."""
        lowered = {name.lower(): value for name, value in headers.items()}
        try:
            limit = int(lowered["x-ratelimit-limit"])
            remaining = int(lowered["x-ratelimit-remaining"])
            reset = datetime.fromtimestamp(int(lowered["x-ratelimit-reset"]), tz=self._clock.now().tzinfo)
        except (KeyError, ValueError, OverflowError, OSError):
            return
        named = lowered.get("x-ratelimit-resource") or bucket
        budget = self._budget(key)
        with budget.lock:
            budget.readings[named] = BudgetReading(named, limit, remaining, reset)

    def spent(self, key: BudgetKey, resume_at: datetime, bucket: str = CORE_BUCKET) -> None:
        """Mark ``key``'s budget spent until ``resume_at``: the forge refused a call for its rate."""
        budget = self._budget(key)
        with budget.lock:
            if budget.spent_until is None or resume_at > budget.spent_until:
                budget.spent_until = resume_at
            self._notice(budget, BudgetNoticeKind.RATE_LIMITED, key, bucket, budget.readings.get(bucket), resume_at)

    def readings(self, key: BudgetKey) -> tuple[BudgetReading, ...]:
        """``key``'s last reading of each bucket, by bucket name."""
        budget = self._budget(key)
        with budget.lock:
            return tuple(budget.readings[name] for name in sorted(budget.readings))

    def forge_readings(self, forge_id: str) -> tuple[tuple[BudgetKey, BudgetReading], ...]:
        """The last reading of each bucket of every budget ``forge_id`` has been heard from under, by secret then
        bucket; the anonymous budget first."""
        with self._guard:
            keys = sorted((key for key in self._budgets if key.forge_id == forge_id), key=_by_secret)
        return tuple((key, reading) for key in keys for reading in self.readings(key))

    def sweeping(self, forge_id: str, secret_name: str | None) -> SweepingGate:
        """The gate ``sweeping`` callers outside the forge seam pass ``(forge_id, secret_name)``'s budget through."""
        return SweepingGate(self, BudgetKey(forge_id, secret_name), self._clock)

    def drain_notices(self) -> tuple[BudgetNotice, ...]:
        """Every notice queued since the last drain, oldest first."""
        with self._guard:
            drained, self._notices = tuple(self._notices), []
        return drained

    def _budget(self, key: BudgetKey) -> _Budget:
        with self._guard:
            return self._budgets.setdefault(key, _Budget())

    def _notice(
        self,
        budget: _Budget,
        kind: BudgetNoticeKind,
        key: BudgetKey,
        bucket: str,
        reading: BudgetReading | None,
        reset: datetime,
    ) -> None:
        """Queue ``kind``'s notice unless one was already queued for this window. Called under ``budget.lock``."""
        window = budget.noticed.get(kind)
        if window is not None and self._clock.now() < window:
            return
        budget.noticed[kind] = reset
        notice = BudgetNotice(
            kind=kind,
            key=key,
            bucket=bucket,
            remaining=reading.remaining if reading is not None else None,
            limit=reading.limit if reading is not None else None,
            reset=reset,
        )
        with self._guard:
            self._notices.append(notice)


def _by_secret(key: BudgetKey) -> tuple[bool, str]:
    return (key.secret_name is not None, key.secret_name or "")


@dataclass(frozen=True)
class SweepingGate:
    """One key's budget bound at ``sweeping``: admission answered rather than raised, refusals read by the one rule."""

    budgets: ForgeBudgets
    key: BudgetKey
    clock: IClock

    def admit(self) -> datetime | None:
        try:
            self.budgets.admit(self.key, ForgePriority.SWEEPING)
        except ForgeBudgetLow as exc:
            return exc.resume_at
        return None

    def learn(self, headers: Mapping[str, str]) -> None:
        self.budgets.learn(self.key, headers)

    def refused(self, status: int, headers: Mapping[str, str], message: str) -> datetime | None:
        resume_at = rate_refusal_resume_at(status, headers, message, self.clock.now())
        if resume_at is not None:
            self.budgets.spent(self.key, resume_at)
        return resume_at


def rate_refusal_resume_at(status: int, headers: Mapping[str, str], message: str, now: datetime) -> datetime | None:
    """When a forge's answer says to come back, or ``None`` when the answer is no rate refusal. Only a ``403`` or a
    ``429`` can be one: with no budget left, a ``Retry-After``, or a secondary limit named in ``message`` — the last
    never lifting sooner than :data:`DEFAULT_RESUME`."""
    if status not in (403, 429):
        return None
    lowered = {name.lower(): value for name, value in headers.items()}
    secondary = "secondary rate limit" in message.lower()
    retry_after = lowered.get("retry-after")
    if lowered.get("x-ratelimit-remaining") != "0" and retry_after is None and not secondary:
        return None
    resume = _seconds_from(now, retry_after) or _reset_at(now, lowered.get("x-ratelimit-reset"))
    resume = resume or now + DEFAULT_RESUME
    return max(resume, now + DEFAULT_RESUME) if secondary else resume


def _seconds_from(now: datetime, raw: str | None) -> datetime | None:
    try:
        seconds = int(raw) if raw is not None else 0
    except ValueError:
        return None
    return now + timedelta(seconds=seconds) if seconds > 0 else None


def _reset_at(now: datetime, raw: str | None) -> datetime | None:
    try:
        reset = datetime.fromtimestamp(int(raw), tz=now.tzinfo) if raw is not None else None
    except (ValueError, OverflowError, OSError):
        return None
    return reset if reset is not None and reset > now else None
