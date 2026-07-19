"""Fleet-registry domain — runner registration, liveness, and the pause brake.

The registry is the fleet's view of its runners: a
runner registers on startup (runner_id + workspace_id) and appears on the board, its
``last_seen_at`` refreshed by registration and the dedicated liveness heartbeat.
Two things derive over the registry, never stored as columns:

* **liveness** — ``last_seen_at`` against a staleness threshold yields online/offline;
  it is time-relative, so it is computed with the injected clock at read time, not on
  the row.
* **paused** — the operator's brake: pause/resume facts append and ``paused``
  derives from the newest one, exactly as a graph's enabled-ness does. The runner
  reads it back on its outbound pull and adheres — pausing stops new leases, in-flight
  chunks run on.

:class:`FleetService` is the domain service the routes delegate to
(``bzh:controller-read-only``); it holds the write registry repository and the injected
clock. The :class:`RunnerRegistration` it returns already carries the **derived**
``paused`` (resolved in the store adapter, like a decision's resolved-ness); liveness is
returned alongside it by the service because it needs the clock.

``token_hash`` (issue #86a) is the one deliberate exception to facts-only
(``bzh:facts-not-status``): the registration row is already a mutable upsert
(``last_seen_at``/``workspace_id`` rewritten in place), so a rotating hash column is
consistent with the rest of the row, unlike the route capability token (#84's
append-only fact table) — a re-enrollment overwrites it, and the prior token stops
resolving immediately. It is minted and rotated by the separate
:class:`~blizzard.hub.domain.enrollment.RunnerEnrollmentService`, not
:class:`FleetService`, since enrollment is an operator act on identity, not a fleet
registration event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import as_utc

_log = get_logger("blizzard.hub.registry")

#: Liveness staleness threshold — a chosen constant, not derived from a formula.
#: The runner-level heartbeat is deliberately much slower than the machine-local worker
#: heartbeat; a runner unheard-from for longer than this reads offline on the board. The
#: runner's reconciliation tick (~30s) refreshes it many times over inside this window.
STALE_AFTER = timedelta(minutes=5)


@dataclass(frozen=True)
class RunnerRegistration:
    """A fleet-registry row with its two **derived** brakes (issue #43).

    They are separate concepts, so they are separate fields rather than one ``paused``:

    * ``hub_paused`` — the fleet's brake, set here and pulled down by the runner, which
      adheres. Advisory today: the hub does not yet refuse a paused runner's claim (#44).
    * ``locally_paused`` — the runner's own brake, set on its machine and reported up. The
      hub only ever reads this one.

    Either stops new claims, so a reader wanting "is it claiming?" wants both.

    ``locally_paused_by``/``locally_paused_reason`` are the newest local-pause fact's cause
    (issue #61's spend-ceiling escalation composes a reason naming the ceiling and the
    spend; a manual ``blizzard runner pause`` carries none) — populated only alongside a
    *true* ``locally_paused`` (``None``/``None`` once resumed or if never paused), so a
    stale cause never renders past its brake clearing.
    """

    runner_id: str
    workspace_id: str
    registered_at: datetime
    last_seen_at: datetime
    hub_paused: bool
    locally_paused: bool = False
    locally_paused_by: str | None = None
    locally_paused_reason: str | None = None
    #: The enrolled bearer token's sha256 hex digest (issue #86a) — ``None`` for an
    #: unenrolled runner. Never the plaintext: the token is returned once, at enroll
    #: time, and this is the only copy the hub ever keeps.
    token_hash: str | None = None
    #: The runner's configured environment-pool size (issue #69) — the ``total`` the board's
    #: slot bar renders ``used/total`` against. A reported **fact** (the runner's own
    #: ``len(workspace_envs)``), refreshed in place on each re-registration, so a config
    #: change converges; ``None`` for a runner registered by a client that predates it.
    env_capacity: int | None = None


@dataclass(frozen=True)
class RunnerLiveness:
    """A registration paired with its clock-relative liveness."""

    registration: RunnerRegistration
    online: bool


def derive_online(last_seen_at: datetime, now: datetime, *, threshold: timedelta) -> bool:
    """True iff the runner was seen within ``threshold`` of ``now``.

    Both operands are coerced to UTC-aware first via :func:`~blizzard.foundation.store.utc.as_utc`
    (idempotent — every store column is ``UtcDateTime``-typed): this is a public pure
    function whose inputs are not guaranteed to come from the store, so the domain keeps
    its own defensive coercion rather than depending on unnamed adapter behavior
    (``bzh:domain-core``).
    """
    return (as_utc(now) - as_utc(last_seen_at)) <= threshold


class IReadRunnerRegistry(Protocol):
    """Read-only registry access — the ``GET /runners`` surface."""

    def get_runner(self, runner_id: str) -> RunnerRegistration | None: ...
    def list_runners(self) -> list[RunnerRegistration]: ...

    def registration_for_token_hash(self, token_hash: str) -> RunnerRegistration | None:
        """The reverse, hash-indexed lookup a presented bearer token resolves through
        (issue #86a) — the mirror image of every other read here, which key on
        ``runner_id``. This is what ``require_runner_principal``
        (``hub/api/auth.py``) resolves a principal with, from the token alone; a
        router-level dependency cannot uniformly read a declared ``runner_id`` (it
        lives in request bodies for some routes, path params for others)."""
        ...


class IWriteRunnerRegistry(IReadRunnerRegistry, Protocol):
    """Read-write registry access — only the domain layer depends on this variant."""

    def upsert_registration(self, runner_id: str, *, workspace_id: str, env_capacity: int | None, at: datetime) -> bool:
        """Register a runner (idempotent upsert), refreshing ``last_seen_at``.

        Returns True if the row was newly created (a first registration), False if it
        already existed and was refreshed — so the caller can emit the right event.

        ``env_capacity`` (issue #69) is the runner's reported environment-pool size,
        written on **both** the insert and the refresh branch so a ``workspace_envs``
        change converges on the next re-registration; an absent value (``None`` from an
        older client) is written verbatim, correctly resetting the stored total to null."""
        ...

    def touch_last_seen(self, runner_id: str, *, at: datetime) -> bool:
        """Refresh a registered runner's ``last_seen_at`` (the heartbeat).

        Returns False if the runner is unknown — a heartbeat before registration."""
        ...

    def record_pause(self, runner_id: str, *, paused: bool, at: datetime, by: str) -> None:
        """Append a fleet pause/resume fact; ``hub_paused`` derives from the newest."""
        ...

    def record_local_pause(
        self, runner_id: str, *, paused: bool, at: datetime, by: str, reason: str | None = None
    ) -> None:
        """Land a runner-reported local pause/start fact; ``locally_paused`` derives (issue #43).

        ``reason`` is the fact's own composed cause (issue #61) — ``None`` for a manual
        pause/start, and always ``None`` on a start (a resume carries no reason)."""
        ...

    def set_token_hash(self, runner_id: str, *, token_hash: str, at: datetime) -> None:
        """Overwrite the registration's bearer-token hash (issue #86a) — a rotation, not
        a fact append (the registration row is already a mutable upsert; see this
        module's docstring). Re-enrolling a runner calls this again: the new hash
        replaces the old one in place, so the prior token stops resolving via
        ``registration_for_token_hash`` immediately. ``at`` is threaded from the
        injected clock (``bzh:injected-clock``) for signature symmetry with this
        seam's other writes; no separate rotation-audit column exists yet to stamp
        it into."""
        ...


class FleetService:
    """Register runners, refresh liveness, and set the declarative pause brake."""

    def __init__(self, *, registry: IWriteRunnerRegistry, clock: IClock, stale_after: timedelta = STALE_AFTER) -> None:
        self._registry = registry
        self._clock = clock
        self._stale_after = stale_after

    def register(self, runner_id: str, workspace_id: str, *, env_capacity: int | None = None) -> bool:
        """Register (or refresh) a runner; returns True on a first registration.

        ``env_capacity`` (issue #69) rides the registration — the runner reports its
        ``len(workspace_envs)`` here, and a re-registration (its heartbeat) converges a
        changed pool. ``None`` from a client that predates the field stores as null."""
        created = self._registry.upsert_registration(
            runner_id, workspace_id=workspace_id, env_capacity=env_capacity, at=self._clock.now()
        )
        _log.info(
            "runner registered",
            runner_id=runner_id,
            workspace_id=workspace_id,
            env_capacity=env_capacity,
            first_time=created,
        )
        return created

    def heartbeat(self, runner_id: str) -> bool:
        """Refresh a runner's liveness; returns False if it is unregistered."""
        return self._registry.touch_last_seen(runner_id, at=self._clock.now())

    def set_paused(self, runner_id: str, *, paused: bool, by: str) -> bool:
        """Flip the fleet's brake for a registered runner; returns False if unknown."""
        if self._registry.get_runner(runner_id) is None:
            return False
        self._registry.record_pause(runner_id, paused=paused, at=self._clock.now(), by=by)
        _log.info("runner pause set", runner_id=runner_id, paused=paused, by=by)
        return True

    def record_local_pause(
        self, runner_id: str, *, paused: bool, at: datetime, by: str, reason: str | None = None
    ) -> None:
        """Land a runner's report that it paused or started *itself* (issue #43).

        Not a control: the runner has already stopped claiming by the time this arrives, and
        the hub cannot set this brake. Landing it is what lets the board show a runner that
        is declining work rather than silently rendering it as running.

        ``reason`` (issue #61) carries the fact's own composed cause — e.g. a spend-ceiling
        crossing names the ceiling and the spend — so an operator sees *why*, not just
        *that*. ``None`` for a manual pause and always on a start.

        Unlike ``set_paused`` this does not require a known runner. The fact rides the
        outbound buffer, which replays an outage in FIFO order, so a pause can legitimately
        arrive before the registration that follows it — dropping it would lose the brake
        exactly when the board most needs it.
        """
        self._registry.record_local_pause(runner_id, paused=paused, at=at, by=by, reason=reason)
        _log.info("runner local pause reported", runner_id=runner_id, paused=paused, by=by, reason=reason)

    def get_liveness(self, runner_id: str) -> RunnerLiveness | None:
        """One runner with its derived liveness — the runner's own pull read."""
        registration = self._registry.get_runner(runner_id)
        if registration is None:
            return None
        return RunnerLiveness(registration=registration, online=self._online(registration))

    def list_with_liveness(self) -> list[RunnerLiveness]:
        """Every registered runner with its derived liveness — the ``GET /runners`` view."""
        return [RunnerLiveness(registration=r, online=self._online(r)) for r in self._registry.list_runners()]

    def _online(self, registration: RunnerRegistration) -> bool:
        return derive_online(registration.last_seen_at, self._clock.now(), threshold=self._stale_after)
