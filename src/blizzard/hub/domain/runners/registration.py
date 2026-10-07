"""Fleet-registry domain — runner addition, registration, liveness, and the pause brake.

A runner's ``runner_id`` is the hub-minted ``rn_`` id, its one identity. Derived rather than stored:
**liveness** (``last_seen_at`` against a staleness threshold, at read time), **never connected** (no
``registered_at``), **paused** and **retired** (the newest appended fact), and **external subscription
usage**. ``token_hash`` is the one mutable exception; a revoked hash is kept as a fact."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.foundation.runner_connection import RunnerConnection
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.foundation.subscription_miss import SampleMissReason
from blizzard.foundation.usage_windows import admit_usage_window
from blizzard.hub.domain.runners.route import Route

#: Liveness staleness threshold — a chosen constant; a runner unheard-from for longer reads offline.
STALE_AFTER = timedelta(minutes=5)

#: External-subscription-usage staleness threshold — deliberately wider than
#: :data:`STALE_AFTER`, since the sample rides a slower cadence than the liveness heartbeat.
EXTERNAL_USAGE_STALE_AFTER = timedelta(minutes=15)


def _usage_stale(sampled_at: datetime, *, now: datetime) -> bool:
    """The per-subscription staleness gate, applied independently for every slug."""
    return (as_utc(now) - as_utc(sampled_at)) > EXTERNAL_USAGE_STALE_AFTER


#: The one miss reason surfaced as a per-slug ``condition``.
CREDENTIAL_LAPSED_CONDITION = SampleMissReason.CREDENTIAL_LAPSED


class RunnerState(StrEnum):
    """A registration's lifecycle state, derived from its row. A runner the hub never added has no
    row and so no state: every operator verb on it is an unknown id. An added runner that has never
    registered has a row and a state, and is never connected (:meth:`RunnerRegistration.never_connected`)."""

    UNENROLLED = "unenrolled"
    ENROLLED = "enrolled"
    #: Retired implies unenrolled — retirement revokes the token in the same pass.
    RETIRED = "retired"


class RunnerVerb(StrEnum):
    """Every verb that acts on a registration. The fleet brake (pause and resume) is one verb:
    a declarative fact appended whichever way it is set."""

    ENROLL = "enroll"
    REVOKE_TOKEN = "revoke-token"
    BRAKE = "brake"
    RETIRE = "retire"
    REINSTATE = "reinstate"
    #: The runner's own contact — every route a runner calls for itself, from registration to federation.
    CONTACT = "contact"


_ACTIVE_VERBS = frozenset({RunnerVerb.BRAKE, RunnerVerb.RETIRE, RunnerVerb.ENROLL, RunnerVerb.CONTACT})

#: Which verbs are legal from which state; :class:`RunnerRegistration` refuses what the state alone cannot.
RUNNER_VERBS: Mapping[RunnerState, frozenset[RunnerVerb]] = {
    RunnerState.UNENROLLED: _ACTIVE_VERBS,
    RunnerState.ENROLLED: _ACTIVE_VERBS | {RunnerVerb.REVOKE_TOKEN},
    RunnerState.RETIRED: frozenset(
        {RunnerVerb.BRAKE, RunnerVerb.RETIRE, RunnerVerb.REINSTATE, RunnerVerb.REVOKE_TOKEN}
    ),
}


@domain_model
@dataclass(frozen=True)
class LifecycleFact:
    """A retire (``retired=True``) or reinstate (``retired=False``) fact to append."""

    runner_id: str
    retired: bool
    at: datetime
    by: str


@domain_model
@dataclass(frozen=True)
class RecordedPause:
    """One pause-family fact as recorded: the fleet brake (``local=False``) or the runner's own
    local brake (``local=True``), engaged (``paused=True``) or released. ``key`` is a natural key
    unique across both brakes' facts; ``reason`` is carried by a local brake alone."""

    key: str
    at: datetime
    runner_id: str
    local: bool
    paused: bool
    by: str | None
    reason: str | None = None


@domain_model
@dataclass(frozen=True)
class TokenRevocation:
    """Revoke the runner's current token: record its hash as revoked and null it, in one write."""

    runner_id: str
    at: datetime
    by: str


@domain_model
@dataclass(frozen=True)
class TokenRotation:
    """Enroll a fresh token hash. Any hash it replaces is recorded as revoked in the same write,
    so rotating is revoking: the old token is refused from that instant."""

    runner_id: str
    token_hash: str
    at: datetime
    by: str


@domain_model
@dataclass(frozen=True)
class RunnerAddition:
    """Add a runner: a never-connected registration under ``runner_id`` and its initial ``name``,
    holding ``token_hash`` — the hash of the bearer token minted with the id — recorded as added
    ``at`` by ``by``."""

    runner_id: str
    name: str
    token_hash: str
    at: datetime
    by: str


def declared_name(name: str | None) -> str | None:
    """The name a registration records: ``name`` without its surrounding whitespace, or ``None`` —
    keeping the name the registry already holds — when the runner declares none or a blank one."""
    return (name or "").strip() or None


@domain_model
@dataclass(frozen=True)
class RunnerRegistration:
    """A fleet-registry row with its two **derived** brakes: ``hub_paused``, the fleet's own,
    which a runner adheres to and which also refuses that runner's claim (#44); and ``locally_paused``,
    the runner's own, which the hub only reads. Either stops new claims, so a reader asking "is it
    claiming?" wants both. ``locally_paused_by``/``_reason`` populate only alongside a *true* brake."""

    runner_id: str
    #: The runner's display name — not unique; nothing keys on it.
    name: str
    added_at: datetime
    hub_paused: bool
    #: Who added the runner; ``None`` when the hub recorded no one.
    added_by: str | None = None
    #: These three stay ``None`` until the runner first registers (:meth:`never_connected`).
    workspace_id: str | None = None
    registered_at: datetime | None = None
    last_seen_at: datetime | None = None
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
    subscription_usage: tuple[SubscriptionUsageSample, ...] = ()
    #: Every declared subscription's newest reported miss, one per slug — unioned with the samples at derive time.
    subscription_usage_misses: tuple[SubscriptionUsageMiss, ...] = ()
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

    def never_connected(self) -> bool:
        """Whether the runner was added but has never registered — no workspace, capabilities,
        or liveness yet. A connection condition, orthogonal to :meth:`state`."""
        return self.registered_at is None

    def state(self) -> RunnerState:
        """The lifecycle state this row derives: retired first, then whether a token is enrolled."""
        if self.retired:
            return RunnerState.RETIRED
        return RunnerState.UNENROLLED if self.token_hash is None else RunnerState.ENROLLED

    def permits(self, verb: RunnerVerb) -> bool:
        """Whether ``verb`` is legal from this registration's state (:data:`RUNNER_VERBS`)."""
        return verb in RUNNER_VERBS[self.state()]

    def refuse_if_retired(self, *, action: str) -> None:
        """Raise :class:`RunnerRetired` when this runner is retired — the one guard every
        operation a retired runner must not perform enforces, whatever token admitted the caller."""
        if not self.permits(RunnerVerb.CONTACT):
            raise RunnerRetired(self.runner_id, action=action)

    def refuse_if_never_connected(self) -> None:
        """Raise :class:`RunnerNeverConnected` when the runner has never registered — its
        declarative state (workspace, capabilities, liveness) does not exist yet."""
        if self.never_connected():
            raise RunnerNeverConnected(self.runner_id)

    def enroll(self, token_hash: str, *, by: str, at: datetime) -> TokenRotation:
        """Mint (or rotate to) ``token_hash``. A retired runner raises :class:`RunnerRetired`:
        ``reinstate`` is the one reinstatement lever."""
        if not self.permits(RunnerVerb.ENROLL):
            raise RunnerRetired(self.runner_id, action="enrollment")
        return TokenRotation(runner_id=self.runner_id, token_hash=token_hash, at=at, by=by)

    def revoke_token(self, *, by: str, at: datetime) -> TokenRevocation:
        """Revoke the held token, leaving the runner added. Raises :class:`RunnerNotEnrolled`
        when it holds none — a retired runner's is normally revoked at retire, but one an enroll
        racing the retire left behind is still revocable."""
        if not self.permits(RunnerVerb.REVOKE_TOKEN) or self.token_hash is None:
            raise RunnerNotEnrolled(self.runner_id)
        return TokenRevocation(runner_id=self.runner_id, at=at, by=by)

    def retire(self, holdings: Sequence[Route], *, force: bool, by: str, at: datetime) -> LifecycleFact | None:
        """The retire fact to append, or ``None`` on a re-run over an already-retired runner,
        which writes no second fact. ``holdings`` are the runner's live routes on chunks that
        still hold a claim; a first retire without ``force`` that finds any raises
        :class:`RunnerHoldsRoutes`. A re-run needs no ``force``: it finishes the release pass."""
        if self.state() is RunnerState.RETIRED:
            return None
        if holdings and not force:
            raise RunnerHoldsRoutes(self.runner_id, list(holdings))
        return LifecycleFact(runner_id=self.runner_id, retired=True, at=at, by=by)

    def reinstate(self, *, by: str, at: datetime) -> LifecycleFact:
        """The ``retired=False`` fact — the reversal and nothing more: the runner stays
        unenrolled. Raises :class:`RunnerNotRetired` unless it is retired."""
        if not self.permits(RunnerVerb.REINSTATE):
            raise RunnerNotRetired(self.runner_id)
        return LifecycleFact(runner_id=self.runner_id, retired=False, at=at, by=by)

    def refuse_federation(self, redirect_uri: str) -> None:
        """Refuse an IdP bounce to ``redirect_uri`` for this runner: :class:`UnregisteredRedirect`
        unless the URI is one it registered (the open-redirect guard), then :class:`RunnerRetired`.
        In that order, so only a caller already holding a registered redirect URI can tell a
        retired runner apart."""
        if not self.is_federation_target(redirect_uri):
            raise UnregisteredRedirect(self.runner_id)
        self.refuse_if_retired(action="federation")

    def is_federation_target(self, redirect_uri: str) -> bool:
        """Whether the IdP may bounce to ``redirect_uri`` for this runner — the URI must be one
        it registered."""
        return redirect_uri in self.redirect_uris


@domain_model
@dataclass(frozen=True)
class RunnerCapability:
    """One harness binding a registered runner reported it can execute —
    the hub-domain mirror of the wire shape, kept import-free of it (``bzh:domain-core``).
    ``version`` is ``None`` when absent; ``default`` marks the runner's own default binding.
    ``available`` defaults ``True``: a binding that does not state otherwise is available."""

    harness_id: str
    version: str | None = None
    tiers: tuple[str, ...] = ()
    default: bool = False
    available: bool = True


@domain_model
@dataclass(frozen=True)
class DeclaredSubscription:
    """One provider subscription a registered runner has declared — the hub-domain mirror
    of the wire shape, kept import-free of it (``bzh:domain-core``). Its slug is the
    roster's own membership key: declared, it is a member whatever the age of its
    sample; dropped, it is not, though its reports persist."""

    slug: str
    name: str
    provider: str


@domain_model
@dataclass(frozen=True)
class RunnerLiveness:
    """A registration paired with its clock-relative liveness."""

    registration: RunnerRegistration
    online: bool

    @classmethod
    def of(cls, registration: RunnerRegistration, *, now: datetime, threshold: timedelta) -> RunnerLiveness:
        """Online iff the runner has registered and was seen within ``threshold`` of ``now``. A
        never-connected runner is offline whatever its ``last_seen_at`` holds, so its liveness and
        :meth:`connection` never disagree.

        Both operands are coerced UTC-aware (``bzh:domain-core``)."""
        seen = registration.last_seen_at
        if registration.never_connected() or seen is None:
            return cls(registration, False)
        return cls(registration, (as_utc(now) - as_utc(seen)) <= threshold)

    def connection(self) -> RunnerConnection:
        """The runner's connection condition: never connected before its first registration,
        else online or offline by its liveness."""
        if self.registration.never_connected():
            return RunnerConnection.NEVER_CONNECTED
        return RunnerConnection.ONLINE if self.online else RunnerConnection.OFFLINE


@domain_model
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

    @classmethod
    def admitted(cls, entry: object) -> ExternalSubscriptionUsageWindow | str:
        """One window off a runner's usage fact, or the field it is refused for
        (:func:`~blizzard.foundation.usage_windows.admit_usage_window`)."""
        admitted = admit_usage_window(entry)
        if isinstance(admitted, str):
            return admitted
        return cls(
            window=admitted.window,
            utilization_pct=admitted.utilization_pct,
            resets_at=admitted.resets_at,
            window_seconds=admitted.window_seconds,
        )

    @property
    def stored(self) -> dict[str, object]:
        """The JSON object ``runner_external_usage`` keeps for this window."""
        return {
            "window": self.window,
            "utilization_pct": self.utilization_pct,
            "resets_at": iso_utc(self.resets_at),
            "window_seconds": self.window_seconds,
        }


@domain_model
@dataclass(frozen=True)
class SubscriptionUsageSample:
    """One declared subscription's newest reported sample, raw —
    staleness is applied per record at derive time, never here, so one dead sampler's
    record cannot blank a healthy sibling's. ``name`` is the declaration's own
    operator-facing label, reported alongside ``slug`` on the fact."""

    slug: str
    name: str
    sampled_at: datetime
    windows: tuple[ExternalSubscriptionUsageWindow, ...]


@domain_model
@dataclass(frozen=True)
class SubscriptionUsageMiss:
    """One declared subscription's newest reported miss, raw — staleness
    is applied at derive time, mirroring :class:`SubscriptionUsageSample`. ``reason`` is the
    sampler's closed-set miss reason; no token, refresh token, or path ever crosses on a
    miss."""

    slug: str
    name: str
    missed_at: datetime
    reason: str


@domain_model
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
    condition: SampleMissReason | None = None
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
        samples: dict[str, SubscriptionUsageSample],
        misses: dict[str, SubscriptionUsageMiss],
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
        samples: dict[str, SubscriptionUsageSample],
        misses: dict[str, SubscriptionUsageMiss],
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
    def _roster_lapsed(sample: SubscriptionUsageSample | None, miss: SubscriptionUsageMiss | None) -> bool:
        """``True`` iff this slug's newest miss is a ``credential_lapsed`` newer than its
        newest (or absent) sample, regardless of either record's age."""
        if miss is None or miss.reason != CREDENTIAL_LAPSED_CONDITION:
            return False
        return sample is None or as_utc(sample.sampled_at) < as_utc(miss.missed_at)

    @staticmethod
    def _rosterless_lapsed(
        sample: SubscriptionUsageSample | None, miss: SubscriptionUsageMiss | None, *, now: datetime
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
        """Every registration, oldest added first — retired runners only when ``include_retired``."""
        ...

    def names_for(self, runner_ids: Iterable[str]) -> dict[str, str]:
        """Each named runner's current name, keyed by its id, in one batched read; an id with no
        registration is absent from the result, never mapped to a placeholder."""
        ...

    def is_token_revoked(self, token_hash: str) -> bool:
        """Whether ``token_hash`` was ever revoked — revoked outright, or rotated away from."""
        ...

    def revoked_token_runner_id(self, token_hash: str) -> str | None:
        """The runner a revoked ``token_hash`` was issued to — the one its newest revocation names —
        or ``None`` when the hash was never revoked; one hash-indexed read. How a refused token
        names the runner it belonged to (:func:`refuse_runner_token`)."""
        ...

    def registration_for_token_hash(self, token_hash: str) -> RunnerRegistration | None:
        """The reverse, hash-indexed lookup a presented bearer token resolves through — the
        mirror image of every other read here, which key on ``runner_id``. A ``runner_id`` is not
        uniformly readable off a request, so a principal resolves from the token alone."""
        ...

    def list_pause_facts_since(self, since: datetime, *, limit: int) -> list[RecordedPause]:
        """Every fact off the fleet's two pause-family fact tables at or after ``since``;
        ``registered``/``heartbeat`` carry no fact table. On this seam,
        not the chunk one (``bzh:repository-split``): a runner-pause fact names no chunk. Each table is
        read with its own ``ORDER BY <ts> DESC, <pk> DESC LIMIT :limit``, never a full scan, so this
        returns up to ``2 * limit`` rows unsorted across the two; the caller merges and re-caps."""
        ...


class IWriteRunnerRegistry(IReadRunnerRegistry, Protocol):
    """Read-write registry access — only the domain layer depends on this variant."""

    def add(self, addition: RunnerAddition) -> None:
        """Insert ``addition`` as a never-connected registration — its id, name, and token hash, with
        ``added_at``/``added_by`` from its ``at``/``by`` and no workspace, registration, or contact.
        The id is the caller's: minted in production, chosen by a test."""
        ...

    def record_registration(
        self,
        runner_id: str,
        *,
        name: str | None = None,
        workspace_id: str,
        env_capacity: int | None,
        public_url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
        capabilities: tuple[RunnerCapability, ...] = (),
        subscriptions: tuple[DeclaredSubscription, ...] | None = None,
        gates: tuple[str, ...] = (),
        at: datetime,
    ) -> bool:
        """Record a registration of the added runner ``runner_id``, refreshing ``last_seen_at``; returns
        True on its first one, which also stamps ``registered_at``. ``name`` replaces the held name
        unless ``None``; every other field is written on **every** registration, absent ones verbatim
        to null/empty, and ``subscriptions`` alone keeps ``None`` vs ``()`` distinct. A ``runner_id``
        with no row raises :class:`LookupError` — a registration only ever follows an add."""
        ...

    def touch_last_seen(self, runner_id: str, *, at: datetime) -> bool:
        """Refresh a registered runner's ``last_seen_at`` (the heartbeat).

        Returns False, writing nothing, if the runner is unknown or has never registered — a
        heartbeat before registration, which must not make a never-connected runner read live."""
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

    def rotate_token(self, rotation: TokenRotation) -> int | None:
        """Set the registration's bearer-token hash, recording any hash it replaces as revoked
        (stamped ``rotation.at``/``rotation.by``) in the same transaction. Returns the
        ``runner_token_revocations.id`` of that revocation, or ``None`` on a first enrollment."""
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
    """The runner is retired — refused whatever token admitted the caller, keyed on its id."""

    def __init__(self, runner_id: str, *, action: str) -> None:
        super().__init__(f"runner {runner_id} is retired — {action} refused; `reinstate` it first")
        self.runner_id = runner_id


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


class RunnerNeverConnected(Exception):
    """A read of a runner's declarative state found a runner added but never registered."""

    def __init__(self, runner_id: str) -> None:
        super().__init__(f"runner {runner_id} has not registered")
        self.runner_id = runner_id


class RunnerNotRetired(Exception):
    """A reinstate targeted a runner that is not retired."""

    def __init__(self, runner_id: str) -> None:
        super().__init__(f"runner {runner_id} is not retired")
        self.runner_id = runner_id


class UnregisteredRedirect(Exception):
    """An IdP bounce named a redirect URI the runner never registered — the open-redirect guard."""

    def __init__(self, runner_id: str) -> None:
        super().__init__(f"runner {runner_id} registered no such redirect_uri")
        self.runner_id = runner_id


class RunnerTokenRefused(Exception):
    """A presented runner bearer token names no runner the hub admits — ``reason`` says why, and
    ``runner_id`` names the runner a revoked or retired token was issued to."""

    def __init__(self, reason: RunnerTokenRefusalReason, *, runner_id: str | None = None) -> None:
        named = "" if runner_id is None else f" for runner {runner_id}"
        super().__init__(f"runner token refused{named}: {reason.value}")
        self.reason = reason
        self.runner_id = runner_id


def refuse_runner_token(
    current: RunnerRegistration | None, *, revoked_for: RunnerRegistration | None
) -> RunnerRegistration:
    """Refuse a presented runner bearer token with :class:`RunnerTokenRefused`; the runner it
    names otherwise. ``current`` is the registration holding the token's hash as its current one;
    ``revoked_for``, read only when none does, is the runner a revoked hash was issued to.

    Retirement outranks revocation, even while the token's hash is still current."""
    if current is not None:
        if current.state() is RunnerState.RETIRED:
            raise RunnerTokenRefused(RunnerTokenRefusalReason.RETIRED, runner_id=current.runner_id)
        return current
    if revoked_for is None:
        raise RunnerTokenRefused(RunnerTokenRefusalReason.UNKNOWN)
    if revoked_for.state() is RunnerState.RETIRED:
        raise RunnerTokenRefused(RunnerTokenRefusalReason.RETIRED, runner_id=revoked_for.runner_id)
    raise RunnerTokenRefused(RunnerTokenRefusalReason.REVOKED, runner_id=revoked_for.runner_id)


class RetiredRunnerGuard:
    """The id-keyed retired-runner refusal — the one domain home for every operation that
    names a runner by id rather than a loaded registration, so each of them refuses a retired
    runner alike."""

    def __init__(self, *, registry: IReadRunnerRegistry) -> None:
        self._registry = registry

    # runner_id resolves the retired-runner guard, a domain rule (bzh:domain-takes-objects).
    # ast-grep-ignore: bzh:domain-takes-objects
    def refuse_if_retired(self, runner_id: str, *, action: str) -> None:
        """Raise :class:`RunnerRetired` when ``runner_id`` names a retired runner. A runner the
        hub never added passes — it has no row, so there is nothing to be retired."""
        registration = self._registry.get_runner(runner_id)
        if registration is not None:
            registration.refuse_if_retired(action=action)
