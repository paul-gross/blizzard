"""Fleet-registry domain — runner registration, liveness, and the pause brake.

Derived rather than stored: **liveness** (``last_seen_at`` against a staleness threshold, at read time),
**paused** and **retired** (the newest appended fact), and **external subscription usage** (by slug, against
its own wider threshold). ``token_hash`` is the one mutable exception; a revoked hash is kept as a fact."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import as_utc
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.work import ActivityRow, holds_claim
from blizzard.wire.facts import CREDENTIAL_LAPSED_MISS_REASON

if TYPE_CHECKING:  # the chunk seams import this module's RunnerRegistration
    from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
    from blizzard.hub.domain.chunks.record import IReadChunkRecordRepository
    from blizzard.hub.domain.chunks.route import IReadChunkRouteRepository
    from blizzard.hub.domain.detach import DetachService

_log = get_logger("blizzard.hub.registry")

#: Liveness staleness threshold — a chosen constant; a runner unheard-from for longer reads offline.
STALE_AFTER = timedelta(minutes=5)

#: External-subscription-usage staleness threshold — deliberately wider than
#: :data:`STALE_AFTER`, since the sample rides a slower cadence than the liveness heartbeat.
EXTERNAL_USAGE_STALE_AFTER = timedelta(minutes=15)


def _usage_stale(sampled_at: datetime, *, now: datetime) -> bool:
    """The per-subscription staleness gate, applied independently for every slug."""
    return (as_utc(now) - as_utc(sampled_at)) > EXTERNAL_USAGE_STALE_AFTER


#: The one miss reason surfaced as a per-slug ``condition``; the runner's own set shares it via ``blizzard.wire``.
CREDENTIAL_LAPSED_CONDITION = CREDENTIAL_LAPSED_MISS_REASON


@dataclass(frozen=True)
class RunnerRegistration:
    """A fleet-registry row with its two **derived** brakes: ``hub_paused``, the fleet's own,
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
    #: The enrolled bearer token's sha256 hex digest — never the plaintext, which the
    #: hub keeps no copy of. ``None`` for an unenrolled runner.
    token_hash: str | None = None
    #: The runner's reported environment-pool size, refreshed in place on each
    #: re-registration so a config change converges; ``None`` when none was reported.
    env_capacity: int | None = None
    #: The runner's own browser-reachable base URL — ``None`` when never registered.
    public_url: str | None = None
    #: The runner's allowed redirect URIs — the open-redirect guard a presented
    #: ``redirect_uri`` is exact-matched against. Empty for a runner that has registered none.
    redirect_uris: tuple[str, ...] = ()
    #: Every declared subscription's newest reported sample, raw, one per slug —
    #: staleness is applied per slug at derive time, not here.
    subscription_usage: tuple[SubscriptionUsageRecord, ...] = ()
    #: Every declared subscription's newest reported miss, one per slug — unioned with the samples at derive time.
    subscription_usage_misses: tuple[SubscriptionUsageMissRecord, ...] = ()
    #: The runner's reported capability snapshot — every harness/tier it can execute right now.
    capabilities: tuple[RunnerCapability, ...] = ()
    #: The declared subscription roster — ``None`` for no roster, ``()`` for none declared.
    declared_subscriptions: tuple[DeclaredSubscription, ...] | None = None
    #: Derived from the newest lifecycle fact; ``retired_at``/``retired_by`` populate only while retired.
    retired: bool = False
    retired_at: datetime | None = None
    retired_by: str | None = None
    #: The node names the runner declared it holds for a human decision — reported, never enforced, by the hub.
    gates: tuple[str, ...] = ()

    def refuse_if_retired(self, *, action: str) -> None:
        """Raise :class:`RunnerRetired` when this runner is retired — the one guard every
        operation a retired runner must not perform enforces, keyed on the id so a token-less
        caller under ``warn`` is refused too."""
        if self.retired:
            raise RunnerRetired(self.runner_id, action=action)

    def is_federation_target(self, redirect_uri: str) -> bool:
        """Whether the IdP may bounce to ``redirect_uri`` for this runner — the URI must be one
        it registered. Retirement is refused separately, once the URI has matched."""
        return redirect_uri in self.redirect_uris


@dataclass(frozen=True)
class RunnerCapability:
    """One harness binding a registered runner reported it can execute —
    the hub-domain mirror of the wire shape, kept import-free of it (``bzh:domain-core``).
    ``version`` is ``None`` when absent; ``default`` marks the runner's own default binding.
    ``available`` defaults ``True`` so a runner asserting none still matches."""

    harness_id: str
    version: str | None = None
    tiers: tuple[str, ...] = ()
    default: bool = False
    available: bool = True


@dataclass(frozen=True)
class DeclaredSubscription:
    """One provider subscription a registered runner has declared — the hub-domain mirror
    of the wire shape, kept import-free of it (``bzh:domain-core``). Its slug is the
    roster's own membership key: declared, it is a member whatever the age of its
    sample; dropped, it is not, though its reports persist."""

    slug: str
    name: str
    provider: str


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
    """One rate-limit window's utilization, read back off ``runner_external_usage``. A
    hub-domain-owned copy rather than a shared import: the hub domain depends on nothing under
    ``blizzard.runner`` (``bzh:domain-core``), so the shape is duplicated at the wire boundary the fact
    already crossed, not shared across it."""

    window: str
    utilization_pct: float
    resets_at: datetime
    window_seconds: int


@dataclass(frozen=True)
class SubscriptionUsageRecord:
    """One declared subscription's newest reported sample, raw —
    staleness is applied per record at derive time, never here, so one dead sampler's
    record cannot blank a healthy sibling's. ``name`` is the declaration's own
    operator-facing label, reported alongside ``slug`` on the fact."""

    slug: str
    name: str
    sampled_at: datetime
    windows: tuple[ExternalSubscriptionUsageWindow, ...]


@dataclass(frozen=True)
class SubscriptionUsageMissRecord:
    """One declared subscription's newest reported miss, raw — staleness
    is applied at derive time, mirroring :class:`SubscriptionUsageRecord`. ``reason`` is the
    sampler's closed-set miss reason; no token, refresh token, or path ever crosses on a
    miss."""

    slug: str
    name: str
    missed_at: datetime
    reason: str


@dataclass(frozen=True)
class PerSubscriptionUsageView:
    """One subscription's usage view — one view per declared slug with a roster, or the
    rosterless age-gated fallback without one. ``sampled_at`` is ``None`` with no surviving
    sample. ``miss_reason``/``missed_at`` carry the slug's newest miss regardless of path,
    whether or not it wins ``condition``."""

    slug: str
    name: str
    sampled_at: datetime | None
    windows: tuple[ExternalSubscriptionUsageWindow, ...]
    #: ``"credential_lapsed"`` when the newest miss outranks the newest sample; ``None`` otherwise.
    condition: str | None = None
    #: The slug's newest reported miss reason; ``None`` when it has none.
    miss_reason: str | None = None
    #: The slug's newest reported miss instant; ``None`` alongside ``miss_reason``.
    missed_at: datetime | None = None

    @classmethod
    def every(cls, registration: RunnerRegistration, *, now: datetime) -> tuple[PerSubscriptionUsageView, ...]:
        """Every subscription's usage view, sorted by slug: a declared roster switches
        membership from age-gated to roster-gated — every declared slug, whatever its
        sample or miss age. Without a roster, see :meth:`_rosterless_views`."""
        samples = {record.slug: record for record in registration.subscription_usage}
        misses = {record.slug: record for record in registration.subscription_usage_misses}
        if registration.declared_subscriptions is not None:
            return cls._roster_views(registration.declared_subscriptions, samples, misses)
        return cls._rosterless_views(samples, misses, now=now)

    @classmethod
    def _roster_views(
        cls,
        roster: tuple[DeclaredSubscription, ...],
        samples: dict[str, SubscriptionUsageRecord],
        misses: dict[str, SubscriptionUsageMissRecord],
    ) -> tuple[PerSubscriptionUsageView, ...]:
        """The roster-gated membership rule — one view per declared slug, no age gate on
        either the sample or the miss; a duplicate declared slug collapses, first wins."""
        declared: dict[str, DeclaredSubscription] = {}
        for declaration in roster:
            declared.setdefault(declaration.slug, declaration)
        views: list[PerSubscriptionUsageView] = []
        for slug in sorted(declared):
            declaration = declared[slug]
            sample = samples.get(slug)
            miss = misses.get(slug)
            views.append(
                cls(
                    slug=slug,
                    name=declaration.name,
                    sampled_at=as_utc(sample.sampled_at) if sample is not None else None,
                    windows=sample.windows if sample is not None else (),
                    condition=CREDENTIAL_LAPSED_CONDITION if cls._roster_lapsed(sample, miss) else None,
                    miss_reason=miss.reason if miss is not None else None,
                    missed_at=as_utc(miss.missed_at) if miss is not None else None,
                )
            )
        return tuple(views)

    @classmethod
    def _rosterless_views(
        cls,
        samples: dict[str, SubscriptionUsageRecord],
        misses: dict[str, SubscriptionUsageMissRecord],
        *,
        now: datetime,
    ) -> tuple[PerSubscriptionUsageView, ...]:
        """The rosterless membership rule, over the **union** of sample and miss rows
        per slug: a non-stale sample or a newest lapsed miss outranking it
        (or an absent sample) admits the slug; a dead or stale
        subscription with only silent (non-lapsed) misses is simply absent. Once admitted,
        a surviving sample's fields are never blanked, stale or lapsed or not."""
        views: list[PerSubscriptionUsageView] = []
        for slug in sorted(set(samples) | set(misses)):
            sample = samples.get(slug)
            miss = misses.get(slug)
            lapsed = cls._rosterless_lapsed(sample, miss, now=now)
            fresh_sample = sample is not None and not _usage_stale(sample.sampled_at, now=now)
            if not lapsed and not fresh_sample:
                continue
            if sample is not None:
                views.append(
                    cls(
                        slug=slug,
                        name=sample.name,
                        sampled_at=as_utc(sample.sampled_at),
                        windows=sample.windows,
                        condition=CREDENTIAL_LAPSED_CONDITION if lapsed else None,
                        miss_reason=miss.reason if miss is not None else None,
                        missed_at=as_utc(miss.missed_at) if miss is not None else None,
                    )
                )
            else:
                assert miss is not None  # narrowed by `lapsed` — `sample is None` forced the `not fresh_sample` skip
                views.append(
                    cls(
                        slug=slug,
                        name=miss.name,
                        sampled_at=None,
                        windows=(),
                        condition=CREDENTIAL_LAPSED_CONDITION,
                        miss_reason=miss.reason,
                        missed_at=as_utc(miss.missed_at),
                    )
                )
        return tuple(views)

    @staticmethod
    def _roster_lapsed(sample: SubscriptionUsageRecord | None, miss: SubscriptionUsageMissRecord | None) -> bool:
        """``True`` iff this slug's newest miss is a ``credential_lapsed`` newer than its
        newest (or absent) sample, regardless of either record's age."""
        if miss is None or miss.reason != CREDENTIAL_LAPSED_CONDITION:
            return False
        return sample is None or as_utc(sample.sampled_at) < as_utc(miss.missed_at)

    @staticmethod
    def _rosterless_lapsed(
        sample: SubscriptionUsageRecord | None, miss: SubscriptionUsageMissRecord | None, *, now: datetime
    ) -> bool:
        """``True`` iff this slug's newest miss is a non-stale ``credential_lapsed`` newer
        than its newest (or absent) sample — the one condition worth surfacing."""
        if miss is None or miss.reason != CREDENTIAL_LAPSED_CONDITION:
            return False
        if sample is not None and as_utc(sample.sampled_at) >= as_utc(miss.missed_at):
            return False
        return not _usage_stale(miss.missed_at, now=now)


class IReadRunnerRegistry(Protocol):
    """Read-only registry access — the ``GET /runners`` surface."""

    def get_runner(self, runner_id: str) -> RunnerRegistration | None: ...
    def list_runners(self, *, include_retired: bool = False) -> list[RunnerRegistration]:
        """Every registration, oldest first — retired runners only when ``include_retired``."""
        ...

    def is_token_revoked(self, token_hash: str) -> bool:
        """Whether ``token_hash`` was ever revoked — a revoked token is refused as revoked,
        never merely left unresolved, since ``warn`` tolerates an unresolved one."""
        ...

    def registration_for_token_hash(self, token_hash: str) -> RunnerRegistration | None:
        """The reverse, hash-indexed lookup a presented bearer token resolves through — the
        mirror image of every other read here, which key on ``runner_id``. A ``runner_id`` is not
        uniformly readable off a request, so a principal resolves from the token alone."""
        ...

    def list_pause_facts_since(self, since: datetime, *, limit: int) -> list[ActivityRow]:
        """Every ``runner-changed`` activity row off the fleet's two pause-family fact tables, at or
        after ``since``; ``registered``/``heartbeat`` carry no fact table. On this seam,
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
        capabilities: tuple[RunnerCapability, ...] = (),
        subscriptions: tuple[DeclaredSubscription, ...] | None = None,
        gates: tuple[str, ...] = (),
        at: datetime,
    ) -> bool:
        """Register a runner (idempotent upsert), refreshing ``last_seen_at``; returns True if the row
        was newly created. ``env_capacity``, ``public_url``/``redirect_uris``, ``capabilities``,
        ``subscriptions``, and ``gates`` are written on **both** branches, so a change converges on
        re-registration; absent writes verbatim to null/empty. ``subscriptions`` alone keeps ``None``
        vs ``()`` distinct, unlike ``capabilities``, which collapses both to null."""
        ...

    def touch_last_seen(self, runner_id: str, *, at: datetime) -> bool:
        """Refresh a registered runner's ``last_seen_at`` (the heartbeat).

        Returns False if the runner is unknown — a heartbeat before registration."""
        ...

    def record_pause(self, runner_id: str, *, paused: bool, at: datetime, by: str) -> int:
        """Append a fleet pause/resume fact; ``hub_paused`` derives from the newest.

        Returns the freshly-written ``runner_pause_facts.id`` (the activity-feed's
        key) — always writes, never a no-op."""
        ...

    def record_local_pause(
        self, runner_id: str, *, paused: bool, at: datetime, by: str, reason: str | None = None
    ) -> int:
        """Land a runner-reported local pause/start fact; ``locally_paused`` derives.

        ``reason`` is the fact's own composed cause — ``None`` for a manual
        pause/start, and always ``None`` on a start (a resume carries no reason). Returns
        the freshly-written ``runner_local_pause_facts.id`` (the activity-feed's key)."""
        ...

    def record_lifecycle(self, runner_id: str, *, retired: bool, at: datetime, by: str) -> int:
        """Append a retire/reinstate fact; ``retired`` derives from the newest. Returns the
        freshly-written ``runner_lifecycle_facts.id`` — always writes, never a no-op."""
        ...

    def revoke_token(self, runner_id: str, *, at: datetime, by: str) -> int | None:
        """Record the runner's current token hash as revoked and null it, in one transaction.
        Returns the ``runner_token_revocations.id``, or ``None`` when no token was enrolled."""
        ...

    def set_token_hash(self, runner_id: str, *, token_hash: str, at: datetime) -> None:
        """Overwrite the registration's bearer-token hash — a rotation, not a fact append.
        Re-enrolling replaces the hash in place, so the prior token stops resolving immediately. ``at``
        is threaded from the injected clock (``bzh:injected-clock``) for signature symmetry with this
        seam's other writes; no rotation-audit column exists yet to stamp it into."""
        ...

    def record_external_usage(
        self, runner_id: str, *, slug: str, name: str, sampled_at: datetime, windows_json: str, at: datetime
    ) -> None:
        """Upsert one declared subscription's newest sampled usage snapshot, keyed on
        ``(runner_id, slug)`` — refresh-in-place, not an append. ``sampled_at`` is the snapshot's own
        reported instant; ``at`` is the landing time (``bzh:injected-clock``). Never requires a known
        runner: a fact for one the registry has not seen lands anyway, and is read once it has."""
        ...

    def record_external_usage_miss(
        self, runner_id: str, *, slug: str, name: str, missed_at: datetime, reason: str, at: datetime
    ) -> None:
        """Upsert one declared subscription's newest reported miss, keyed on
        ``(runner_id, slug)`` — refresh-in-place, mirroring :meth:`record_external_usage`. The
        sample row is left untouched: this is a sibling table, not an overwrite of it."""
        ...


class RunnerRetired(Exception):
    """The runner is retired — refused regardless of auth mode, keyed on its id."""

    def __init__(self, runner_id: str, *, action: str) -> None:
        super().__init__(f"runner {runner_id} is retired — {action} refused; `reinstate` it first")
        self.runner_id = runner_id


class RetiredRunnerGuard:
    """The id-keyed retired-runner refusal — the one domain home for every operation that
    names a runner by id rather than a loaded registration, so a token-less caller under
    ``warn`` is refused on each of them alike."""

    def __init__(self, *, registry: IReadRunnerRegistry) -> None:
        self._registry = registry

    # runner_id resolves the retired-runner guard, a domain rule (bzh:domain-takes-objects).
    # ast-grep-ignore: bzh:domain-takes-objects
    def refuse_if_retired(self, runner_id: str, *, action: str) -> None:
        """Raise :class:`RunnerRetired` when ``runner_id`` names a retired runner. An
        unregistered runner passes — there is nothing to be retired."""
        registration = self._registry.get_runner(runner_id)
        if registration is not None:
            registration.refuse_if_retired(action=action)


class RunnerHoldsRoutes(Exception):
    """A retire without ``force`` found live routes — each held chunk and its environments."""

    def __init__(self, runner_id: str, holdings: list[Route]) -> None:
        held = "; ".join(f"{r.chunk_id} (environments: {', '.join(r.environment_ids) or 'none'})" for r in holdings)
        super().__init__(f"runner {runner_id} holds {len(holdings)} chunk(s): {held} — retire with --force to release")
        self.runner_id = runner_id
        self.holdings = holdings


class RunnerNotEnrolled(Exception):
    """A token revocation targeted a runner with no enrolled token."""

    def __init__(self, runner_id: str) -> None:
        super().__init__(f"runner {runner_id} has no enrolled token to revoke")
        self.runner_id = runner_id


class RunnerNotRetired(Exception):
    """A reinstate targeted a runner that is not retired."""

    def __init__(self, runner_id: str) -> None:
        super().__init__(f"runner {runner_id} is not retired")
        self.runner_id = runner_id


@dataclass(frozen=True)
class ReleasedRoute:
    """One route the retire release pass released — the chunk and its ``route_released.id``."""

    chunk_id: str
    released_id: int


@dataclass(frozen=True)
class RetireOutcome:
    """What one retire wrote: ``fact_id`` is ``None`` on a re-run over an already-retired
    runner, ``revocation_id`` ``None`` when no token was enrolled."""

    fact_id: int | None
    revocation_id: int | None
    released: tuple[ReleasedRoute, ...]


class FleetService:
    """Register runners, refresh liveness, set the declarative pause brake, and retire."""

    def __init__(
        self,
        *,
        registry: IWriteRunnerRegistry,
        routes: IReadChunkRouteRepository,
        records: IReadChunkRecordRepository,
        facts: IReadChunkFactsRepository,
        detach: DetachService,
        retired: RetiredRunnerGuard,
        clock: IClock,
        stale_after: timedelta = STALE_AFTER,
    ) -> None:
        self._registry = registry
        # Retirement's holdings read and release pass — the hub's existing detach path.
        self._routes = routes
        self._records = records
        self._facts = facts
        self._detach = detach
        self._retired = retired
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
        capabilities: tuple[RunnerCapability, ...] = (),
        subscriptions: tuple[DeclaredSubscription, ...] | None = None,
        gates: tuple[str, ...] = (),
    ) -> bool:
        """Register (or refresh) a runner; returns True on a first registration.

        The runner's reported facts (``env_capacity``, ``public_url``/``redirect_uris``,
        ``capabilities``, ``subscriptions``, ``gates``) are overwritten on every registration; absent
        values store as null/empty. A retired runner raises :class:`RunnerRetired` before any write."""
        self._retired.refuse_if_retired(runner_id, action="registration")
        created = self._registry.upsert_registration(
            runner_id,
            workspace_id=workspace_id,
            env_capacity=env_capacity,
            public_url=public_url,
            redirect_uris=redirect_uris,
            capabilities=capabilities,
            subscriptions=subscriptions,
            gates=gates,
            at=self._clock.now(),
        )
        _log.info(
            "runner registered",
            runner_id=runner_id,
            workspace_id=workspace_id,
            env_capacity=env_capacity,
            public_url=public_url,
            capabilities=[c.harness_id for c in capabilities],
            subscriptions=None if subscriptions is None else [s.slug for s in subscriptions],
            first_time=created,
        )
        return created

    def heartbeat(self, runner_id: str) -> bool:
        """Refresh a runner's liveness; returns False if it is unregistered. A retired runner
        is refused with :class:`RunnerRetired` before its liveness is touched."""
        self._retired.refuse_if_retired(runner_id, action="heartbeat")
        return self._registry.touch_last_seen(runner_id, at=self._clock.now())

    def retire(self, registration: RunnerRegistration, *, by: str, force: bool) -> RetireOutcome:
        """Record the fact and revoke the token first, so claims are refused from that instant,
        then release every held route through ``DetachService`` — a terminal chunk holds none. A
        first retire without ``force`` refuses with :class:`RunnerHoldsRoutes`; a re-run writes no
        second fact and re-runs the release pass, which also catches a claim that slipped past the pre-lock check."""
        runner_id = registration.runner_id
        if not registration.retired and not force:
            holdings = self._holdings(runner_id)
            if holdings:
                raise RunnerHoldsRoutes(runner_id, holdings)
        now = self._clock.now()
        fact_id = None
        if not registration.retired:
            fact_id = self._registry.record_lifecycle(runner_id, retired=True, at=now, by=by)
        revocation_id = self._registry.revoke_token(runner_id, at=now, by=by)
        released = tuple(self._release(route) for route in self._routes.live_routes_of_runner(runner_id))
        outcome = RetireOutcome(
            fact_id=fact_id, revocation_id=revocation_id, released=tuple(r for r in released if r is not None)
        )
        _log.info(
            "runner retired",
            runner_id=runner_id,
            by=by,
            force=force,
            rerun=fact_id is None,
            released=[r.chunk_id for r in outcome.released],
        )
        return outcome

    def _holdings(self, runner_id: str) -> list[Route]:
        """The runner's live routes on chunks that still hold a claim — a route left on a
        terminal chunk is no holding."""
        routes = self._routes.live_routes_of_runner(runner_id)
        facts = self._facts.status_facts_for([route.chunk_id for route in routes])
        return [route for route in routes if route.chunk_id not in facts or holds_claim(facts[route.chunk_id].status())]

    def _release(self, route: Route) -> ReleasedRoute | None:
        chunk = self._records.get(route.chunk_id)
        if chunk is None:  # pragma: no cover - a routed chunk always has its record
            return None
        released_id = self._detach.release_held(chunk, runner_id=route.runner_id)
        if released_id is None:  # released (or re-claimed elsewhere) since the holdings read
            return None
        return ReleasedRoute(chunk_id=route.chunk_id, released_id=released_id)

    def reinstate(self, registration: RunnerRegistration, *, by: str) -> int:
        """Record a ``retired=False`` fact, returning its id. The runner stays unenrolled —
        its token was revoked at retire — so the operator enrolls it afresh."""
        if not registration.retired:
            raise RunnerNotRetired(registration.runner_id)
        fact_id = self._registry.record_lifecycle(registration.runner_id, retired=False, at=self._clock.now(), by=by)
        _log.info("runner reinstated", runner_id=registration.runner_id, by=by)
        return fact_id

    def revoke_token(self, registration: RunnerRegistration, *, by: str) -> int:
        """Revoke the runner's current token, leaving it registered; returns the revocation id.
        Refuses with :class:`RunnerNotEnrolled` when it holds none."""
        if registration.token_hash is None:
            raise RunnerNotEnrolled(registration.runner_id)
        revocation_id = self._registry.revoke_token(registration.runner_id, at=self._clock.now(), by=by)
        if revocation_id is None:  # revoked concurrently between the read and the write
            raise RunnerNotEnrolled(registration.runner_id)
        _log.info("runner token revoked", runner_id=registration.runner_id, by=by)
        return revocation_id

    def set_paused(self, registration: RunnerRegistration, *, paused: bool, by: str) -> int:
        """Flip the fleet's brake for a registered runner, returning the freshly-written
        ``runner_pause_facts.id`` (the activity-feed's key). Takes the loaded
        registration (``bzh:domain-takes-objects``) — the edge resolves ``runner_id`` to
        it (404 if unknown) before calling this."""
        fact_id = self._registry.record_pause(registration.runner_id, paused=paused, at=self._clock.now(), by=by)
        _log.info("runner pause set", runner_id=registration.runner_id, paused=paused, by=by)
        return fact_id

    def record_local_pause(
        self, runner_id: str, *, paused: bool, at: datetime, by: str, reason: str | None = None
    ) -> int:
        """Land a runner's report that it paused or started *itself* — not a control: the
        runner has already stopped claiming, and the hub cannot set this brake. ``reason``
        carries the fact's own composed cause, ``None`` for a manual pause and always on a start. Unlike
        ``set_paused`` this does not require a known runner: the buffer replays an outage in FIFO order,
        so a pause can legitimately arrive before the registration that follows it."""
        fact_id = self._registry.record_local_pause(runner_id, paused=paused, at=at, by=by, reason=reason)
        _log.info("runner local pause reported", runner_id=runner_id, paused=paused, by=by, reason=reason)
        return fact_id

    def record_external_usage(
        self, runner_id: str, *, slug: str, name: str, sampled_at: datetime, windows_json: str, at: datetime
    ) -> None:
        """Land one declared subscription's reported usage sample —
        refresh-in-place per ``(runner_id, slug)``, mirroring :meth:`record_local_pause`'s
        no-known-runner-required acceptance: the fact rides the same outbound buffer, so
        it can legitimately arrive ahead of the registration that follows it."""
        self._registry.record_external_usage(
            runner_id, slug=slug, name=name, sampled_at=sampled_at, windows_json=windows_json, at=at
        )
        _log.info("runner external usage sample landed", runner_id=runner_id, slug=slug, sampled_at=sampled_at)

    def record_external_usage_miss(
        self, runner_id: str, *, slug: str, name: str, missed_at: datetime, reason: str, at: datetime
    ) -> None:
        """Land one declared subscription's reported miss — refresh-in-place
        per ``(runner_id, slug)``, mirroring :meth:`record_external_usage`'s own
        no-known-runner-required acceptance."""
        self._registry.record_external_usage_miss(
            runner_id, slug=slug, name=name, missed_at=missed_at, reason=reason, at=at
        )
        _log.info(
            "runner external usage miss landed", runner_id=runner_id, slug=slug, reason=reason, missed_at=missed_at
        )

    def get_liveness(self, registration: RunnerRegistration) -> RunnerLiveness:
        """One runner's derived liveness over its loaded registration
        (``bzh:domain-takes-objects``) — the edge resolves ``runner_id`` to it (404 if
        unknown) before calling this."""
        return self._liveness(registration)

    def own_liveness(self, registration: RunnerRegistration) -> RunnerLiveness:
        """The runner's own pull read of its liveness — refused with :class:`RunnerRetired`
        when it is retired, unlike the operator's :meth:`get_liveness`, which still shows it."""
        registration.refuse_if_retired(action="runner read")
        return self._liveness(registration)

    def list_with_liveness(self, *, include_retired: bool = False) -> list[RunnerLiveness]:
        """Every registered runner with its derived liveness — the ``GET /runners`` view;
        retired runners only when ``include_retired``."""
        return [self._liveness(r) for r in self._registry.list_runners(include_retired=include_retired)]

    def _liveness(self, registration: RunnerRegistration) -> RunnerLiveness:
        return RunnerLiveness.of(registration, now=self._clock.now(), threshold=self._stale_after)
