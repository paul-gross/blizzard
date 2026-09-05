"""Fleet-registry domain — runner registration, liveness, and the pause brake.

Three things derive over the registry rather than being stored: **liveness** (``last_seen_at`` against a
staleness threshold, clock-relative so it is computed at read time), **paused** (the newest appended
pause/resume fact), and **external subscription usage** (issue #218, each declared subscription's newest
sample against its own wider threshold, independently by slug since blizzard#436 phase 3). ``token_hash``
is the one exception to facts-only: the row is already a mutable upsert."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import as_utc
from blizzard.hub.domain.work import ActivityRow

_log = get_logger("blizzard.hub.registry")

#: Liveness staleness threshold — a chosen constant; a runner unheard-from for longer reads offline.
STALE_AFTER = timedelta(minutes=5)

#: External-subscription-usage staleness threshold (issue #218) — deliberately wider than
#: :data:`STALE_AFTER`, since the sample rides a slower cadence than the liveness heartbeat.
EXTERNAL_USAGE_STALE_AFTER = timedelta(minutes=15)

#: Mirrors ``blizzard.runner.config.LEGACY_ANTHROPIC_SLUG`` — restated rather than
#: imported (``bzh:domain-core``: the hub domain depends on nothing under
#: ``blizzard.runner``). The one slug a runner with no ``[[subscription]]`` declared
#: reports under, and the slug ``RunnerRegistration.legacy_subscription_usage`` derives
#: the old singular ``RunnerView.external_subscription_usage`` field from.
LEGACY_ANTHROPIC_SLUG = "anthropic"


@dataclass(frozen=True)
class RunnerRegistration:
    """A fleet-registry row with its two **derived** brakes (issue #43): ``hub_paused``, the fleet's own,
    which a runner adheres to and which also refuses that runner's claim (#44); and ``locally_paused``,
    the runner's own, which the hub only reads. Either stops new claims, so a reader asking "is it
    claiming?" wants both. ``locally_paused_by``/``_reason`` populate only alongside a *true* brake."""

    runner_id: str
    workspace_id: str
    registered_at: datetime
    last_seen_at: datetime
    hub_paused: bool
    locally_paused: bool = False
    locally_paused_by: str | None = None
    locally_paused_reason: str | None = None
    #: The enrolled bearer token's sha256 hex digest (issue #86a) — never the plaintext, which the
    #: hub keeps no copy of. ``None`` for an unenrolled runner.
    token_hash: str | None = None
    #: The runner's reported environment-pool size (issue #69), refreshed in place on each
    #: re-registration so a config change converges; ``None`` when none was reported.
    env_capacity: int | None = None
    #: The runner's own browser-reachable base URL (issue #95) — ``None`` when never registered.
    public_url: str | None = None
    #: The runner's allowed redirect URIs (issue #95) — the open-redirect guard a presented
    #: ``redirect_uri`` is exact-matched against. Empty for a runner that has registered none.
    redirect_uris: tuple[str, ...] = ()
    #: Every declared subscription's newest reported sample, raw, one per slug (issue #218,
    #: blizzard#436 phase 3) — staleness is applied per slug at derive time
    #: (:meth:`ExternalSubscriptionUsageView.of`, :meth:`SubscriptionUsageView.every`), not here.
    #: Empty for a runner that has never sampled anything.
    subscription_usage: tuple[SubscriptionUsageRecord, ...] = ()

    def usage_record(self, slug: str) -> SubscriptionUsageRecord | None:
        """This runner's newest raw sample for ``slug``, or ``None`` if it has never
        reported one — the lookup :meth:`ExternalSubscriptionUsageView.of` and
        :meth:`SubscriptionUsageView.every` both key off."""
        return next((r for r in self.subscription_usage if r.slug == slug), None)


@dataclass(frozen=True)
class RunnerLiveness:
    """A registration paired with its clock-relative liveness."""

    registration: RunnerRegistration
    online: bool

    @classmethod
    def of(cls, registration: RunnerRegistration, *, now: datetime, threshold: timedelta) -> RunnerLiveness:
        """Online iff the runner was seen within ``threshold`` of ``now``.

        Both operands are coerced UTC-aware via :func:`~blizzard.foundation.store.utc.as_utc`
        (idempotent) rather than depending on unnamed adapter behavior (``bzh:domain-core``)."""
        return cls(registration, (as_utc(now) - as_utc(registration.last_seen_at)) <= threshold)


@dataclass(frozen=True)
class ExternalSubscriptionUsageWindow:
    """One rate-limit window's utilization, read back off ``runner_external_usage`` (issue #218). A
    hub-domain-owned copy rather than a shared import: the hub domain depends on nothing under
    ``blizzard.runner`` (``bzh:domain-core``), so the shape is duplicated at the wire boundary the fact
    already crossed, not shared across it."""

    window: str
    utilization_pct: float
    resets_at: datetime
    window_seconds: int


@dataclass(frozen=True)
class SubscriptionUsageRecord:
    """One declared subscription's newest reported sample, raw (blizzard#436 phase 3) —
    staleness is applied per record at derive time, never here, so one dead sampler's
    record cannot blank a healthy sibling's. ``name`` is the declaration's own
    operator-facing label, reported alongside ``slug`` on the fact."""

    slug: str
    name: str
    sampled_at: datetime
    windows: tuple[ExternalSubscriptionUsageWindow, ...]


@dataclass(frozen=True)
class ExternalSubscriptionUsageView:
    """One subscription's usage sample, already past its own staleness gate."""

    sampled_at: datetime
    windows: tuple[ExternalSubscriptionUsageWindow, ...]

    @classmethod
    def of(cls, registration: RunnerRegistration, *, slug: str, now: datetime) -> ExternalSubscriptionUsageView | None:
        """The renderable view (issue #218) for ``slug``, or ``None`` — never a fabricated
        zero one.

        ``None`` for a runner that has never sampled ``slug``, or whose newest sample for
        it is older than :data:`EXTERNAL_USAGE_STALE_AFTER` relative to ``now``. Evaluated
        independently per ``slug`` (blizzard#436 phase 3): a stale or absent sibling never
        affects this one."""
        record = registration.usage_record(slug)
        if record is None:
            return None
        sampled_at = as_utc(record.sampled_at)
        if (as_utc(now) - sampled_at) > EXTERNAL_USAGE_STALE_AFTER:
            return None
        return cls(sampled_at=sampled_at, windows=record.windows)


@dataclass(frozen=True)
class SubscriptionUsageView:
    """One subscription's usage, past its own staleness gate, carrying its identity
    (blizzard#436 phase 3) — the wire's additive per-subscription collection, beside the
    single legacy :class:`ExternalSubscriptionUsageView`."""

    slug: str
    name: str
    sampled_at: datetime
    windows: tuple[ExternalSubscriptionUsageWindow, ...]

    @classmethod
    def every(cls, registration: RunnerRegistration, *, now: datetime) -> tuple[SubscriptionUsageView, ...]:
        """Every declared subscription's non-stale view, in the registration's own
        recorded order — one dead or stale subscription is simply absent from this
        collection, never a reason to omit any other."""
        views: list[SubscriptionUsageView] = []
        for record in registration.subscription_usage:
            sampled_at = as_utc(record.sampled_at)
            if (as_utc(now) - sampled_at) > EXTERNAL_USAGE_STALE_AFTER:
                continue
            views.append(cls(slug=record.slug, name=record.name, sampled_at=sampled_at, windows=record.windows))
        return tuple(views)


class IReadRunnerRegistry(Protocol):
    """Read-only registry access — the ``GET /runners`` surface."""

    def get_runner(self, runner_id: str) -> RunnerRegistration | None: ...
    def list_runners(self) -> list[RunnerRegistration]: ...

    def registration_for_token_hash(self, token_hash: str) -> RunnerRegistration | None:
        """The reverse, hash-indexed lookup a presented bearer token resolves through (issue #86a) — the
        mirror image of every other read here, which key on ``runner_id``. A ``runner_id`` is not
        uniformly readable off a request, so a principal resolves from the token alone."""
        ...

    def list_pause_facts_since(self, since: datetime, *, limit: int) -> list[ActivityRow]:
        """Every ``runner-changed`` activity row off the fleet's two pause-family fact tables, at or
        after ``since`` (issue #213); ``registered``/``heartbeat`` carry no fact table. On this seam,
        not the chunk one (``bzh:repository-split``): a runner-pause fact names no chunk. Each table is
        read with its own ``ORDER BY <ts> DESC, <pk> DESC LIMIT :limit``, never a full scan, so this
        returns up to ``2 * limit`` rows unsorted across the two; the caller merges and re-caps."""
        ...


class IWriteRunnerRegistry(IReadRunnerRegistry, Protocol):
    """Read-write registry access — only the domain layer depends on this variant."""

    def upsert_registration(
        self,
        runner_id: str,
        *,
        workspace_id: str,
        env_capacity: int | None,
        public_url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
        at: datetime,
    ) -> bool:
        """Register a runner (idempotent upsert), refreshing ``last_seen_at``; returns True if the row
        was newly created. ``env_capacity`` (issue #69) and ``public_url``/``redirect_uris`` (issue #95)
        are written on **both** the insert and the refresh branch, so a change converges on the next
        re-registration; an absent value is written verbatim, resetting the stored field to null."""
        ...

    def touch_last_seen(self, runner_id: str, *, at: datetime) -> bool:
        """Refresh a registered runner's ``last_seen_at`` (the heartbeat).

        Returns False if the runner is unknown — a heartbeat before registration."""
        ...

    def record_pause(self, runner_id: str, *, paused: bool, at: datetime, by: str) -> int:
        """Append a fleet pause/resume fact; ``hub_paused`` derives from the newest.

        Returns the freshly-written ``runner_pause_facts.id`` (issue #213's activity-feed
        key) — always writes, never a no-op."""
        ...

    def record_local_pause(
        self, runner_id: str, *, paused: bool, at: datetime, by: str, reason: str | None = None
    ) -> int:
        """Land a runner-reported local pause/start fact; ``locally_paused`` derives (issue #43).

        ``reason`` is the fact's own composed cause (issue #61) — ``None`` for a manual
        pause/start, and always ``None`` on a start (a resume carries no reason). Returns
        the freshly-written ``runner_local_pause_facts.id`` (issue #213's activity-feed key)."""
        ...

    def set_token_hash(self, runner_id: str, *, token_hash: str, at: datetime) -> None:
        """Overwrite the registration's bearer-token hash (issue #86a) — a rotation, not a fact append.
        Re-enrolling replaces the hash in place, so the prior token stops resolving immediately. ``at``
        is threaded from the injected clock (``bzh:injected-clock``) for signature symmetry with this
        seam's other writes; no rotation-audit column exists yet to stamp it into."""
        ...

    def record_external_usage(
        self, runner_id: str, *, slug: str, name: str, sampled_at: datetime, windows_json: str, at: datetime
    ) -> None:
        """Upsert one declared subscription's newest sampled usage snapshot (issue #218,
        keyed on ``(runner_id, slug)`` since blizzard#436 phase 3) — refresh-in-place, not
        an append. ``sampled_at`` is the snapshot's own reported instant; ``at`` is the
        landing time (``bzh:injected-clock``). Unlike ``upsert_registration``, never
        requires a known runner: a fact for one the registry has not seen lands anyway,
        and is read once it has."""
        ...


class FleetService:
    """Register runners, refresh liveness, and set the declarative pause brake."""

    def __init__(self, *, registry: IWriteRunnerRegistry, clock: IClock, stale_after: timedelta = STALE_AFTER) -> None:
        self._registry = registry
        self._clock = clock
        self._stale_after = stale_after

    def register(
        self,
        runner_id: str,
        workspace_id: str,
        *,
        env_capacity: int | None = None,
        public_url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
    ) -> bool:
        """Register (or refresh) a runner; returns True on a first registration.

        ``env_capacity`` (issue #69) and ``public_url``/``redirect_uris`` (issue #95) are the runner's
        own reported facts, unconditionally overwritten on every (re-)registration so a change
        converges; ``None`` from a client that predates a field stores as null."""
        created = self._registry.upsert_registration(
            runner_id,
            workspace_id=workspace_id,
            env_capacity=env_capacity,
            public_url=public_url,
            redirect_uris=redirect_uris,
            at=self._clock.now(),
        )
        _log.info(
            "runner registered",
            runner_id=runner_id,
            workspace_id=workspace_id,
            env_capacity=env_capacity,
            public_url=public_url,
            first_time=created,
        )
        return created

    def heartbeat(self, runner_id: str) -> bool:
        """Refresh a runner's liveness; returns False if it is unregistered."""
        return self._registry.touch_last_seen(runner_id, at=self._clock.now())

    def set_paused(self, runner_id: str, *, paused: bool, by: str) -> int | None:
        """Flip the fleet's brake for a registered runner; returns ``None`` if unknown,
        else the freshly-written ``runner_pause_facts.id`` (issue #213's activity-feed
        key)."""
        if self._registry.get_runner(runner_id) is None:
            return None
        fact_id = self._registry.record_pause(runner_id, paused=paused, at=self._clock.now(), by=by)
        _log.info("runner pause set", runner_id=runner_id, paused=paused, by=by)
        return fact_id

    def record_local_pause(
        self, runner_id: str, *, paused: bool, at: datetime, by: str, reason: str | None = None
    ) -> int:
        """Land a runner's report that it paused or started *itself* (issue #43) — not a control: the
        runner has already stopped claiming, and the hub cannot set this brake. ``reason`` (issue #61)
        carries the fact's own composed cause, ``None`` for a manual pause and always on a start. Unlike
        ``set_paused`` this does not require a known runner: the buffer replays an outage in FIFO order,
        so a pause can legitimately arrive before the registration that follows it."""
        fact_id = self._registry.record_local_pause(runner_id, paused=paused, at=at, by=by, reason=reason)
        _log.info("runner local pause reported", runner_id=runner_id, paused=paused, by=by, reason=reason)
        return fact_id

    def record_external_usage(
        self, runner_id: str, *, slug: str, name: str, sampled_at: datetime, windows_json: str, at: datetime
    ) -> None:
        """Land one declared subscription's reported usage sample (issue #218) —
        refresh-in-place per ``(runner_id, slug)``, mirroring :meth:`record_local_pause`'s
        no-known-runner-required acceptance: the fact rides the same outbound buffer, so
        it can legitimately arrive ahead of the registration that follows it."""
        self._registry.record_external_usage(
            runner_id, slug=slug, name=name, sampled_at=sampled_at, windows_json=windows_json, at=at
        )
        _log.info("runner external usage sample landed", runner_id=runner_id, slug=slug, sampled_at=sampled_at)

    def get_liveness(self, runner_id: str) -> RunnerLiveness | None:
        """One runner with its derived liveness — the runner's own pull read."""
        registration = self._registry.get_runner(runner_id)
        if registration is None:
            return None
        return self._liveness(registration)

    def list_with_liveness(self) -> list[RunnerLiveness]:
        """Every registered runner with its derived liveness — the ``GET /runners`` view."""
        return [self._liveness(r) for r in self._registry.list_runners()]

    def _liveness(self, registration: RunnerRegistration) -> RunnerLiveness:
        return RunnerLiveness.of(registration, now=self._clock.now(), threshold=self._stale_after)
