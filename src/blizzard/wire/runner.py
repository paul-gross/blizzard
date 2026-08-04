"""Fleet-registry wire bodies.

The registry surface: a runner registers (``POST /runners``) and heartbeats
(``POST /runners/{id}/heartbeats``); ``GET /runners`` lists the fleet with liveness;
``POST /runners/{id}/pause`` / ``/resume`` set the pause brake; ``GET /runners/{id}``
reads one runner's declarative state back. ``online`` and ``paused`` are **derived** —
liveness from ``last_seen_at`` against the staleness threshold, paused from the newest
pause fact.

``POST /runners/{id}/enrollments`` (issue #86a) mints or rotates the runner's bearer
token, returning :class:`RunnerEnrollmentResponse` — the one response that ever carries
the plaintext.
"""

from __future__ import annotations

from pydantic import BaseModel


class RunnerRegistrationRequest(BaseModel):
    """Register a runner into the fleet — runner id + workspace binding.

    ``env_capacity`` is the runner's configured environment-pool size (the length of its
    ``workspace_envs``); ``None`` when the client reports none, never a guessed total.
    Re-registration overwrites the stored value unconditionally, so a ``workspace_envs``
    change converges on the next one."""

    runner_id: str
    workspace_id: str
    env_capacity: int | None = None
    #: The runner's own browser-reachable base URL (issue #95) — optional; a runner
    #: that never registers one cannot be an IdP-authorize ``client`` (the hub has no
    #: registered redirect to validate a bounce against). Recorded verbatim on every
    #: (re-)registration, like ``env_capacity``.
    url: str | None = None
    #: The allowed redirect URIs the hub's IdP authorize endpoint may bounce a browser
    #: to for this runner (issue #95) — exact-match only (the open-redirect guard).
    #: Empty registers none.
    redirect_uris: list[str] = []


class RunnerRegistrationResponse(BaseModel):
    """The registered runner's id, and whether this call first created its row."""

    runner_id: str
    first_registration: bool


class RunnerEnrollmentResponse(BaseModel):
    """A freshly minted (or rotated) bearer token — issue #86a.

    ``token`` is the plaintext; the hub keeps only its sha256 hash from here on, so
    this response is the one and only place it is ever visible again. A re-enroll
    call rotates: the old token stops resolving the moment this response lands."""

    runner_id: str
    token: str


class ExternalSubscriptionUsageWindowView(BaseModel):
    """One rate-limit window's utilization, as the harness's own account reported it
    (issue #218) — ``window`` is the harness-native label (``"5h"``/``"7d"`` for Claude
    Code), ``utilization_pct`` is 0-100, ``resets_at`` the window's reset instant, and
    ``window_seconds`` its length, carried alongside the label so a reader never has
    to hardcode the mapping back."""

    window: str
    utilization_pct: float
    resets_at: str
    window_seconds: int


class ExternalSubscriptionUsageView(BaseModel):
    """A runner's newest sampled external-subscription-usage snapshot (issue #218)."""

    sampled_at: str
    windows: list[ExternalSubscriptionUsageWindowView]


class RunnerView(BaseModel):
    """One fleet-registry row — derived liveness, both brakes, and (issue #218) an
    advisory external-usage snapshot.

    A runner can be paused by two different parties for two different reasons, so the two
    are reported separately rather than collapsed into one ``paused`` (issues #43, #45).
    A reader that wants "is it claiming?" ORs them; ``hub_paused`` is claims-only, while
    ``locally_paused`` alone answers "is it spawning anything at all?". ``external_subscription_usage``
    is a third, unrelated kind of thing carried on the same row: a read-only diagnostic
    of the harness's own subscription rate-limit windows, never a brake and never
    consulted by scheduling or claiming.
    """

    runner_id: str
    workspace_id: str
    registered_at: str
    last_seen_at: str
    online: bool
    hub_paused: bool  # the fleet paused it — `blizzard hub pause`, cleared by `hub resume`
    locally_paused: bool = False  # it paused itself — spawns nothing, `blizzard runner pause`/`start`
    # The local pause's own cause, populated only alongside a true `locally_paused` (issue
    # #61): `by` is "operator" for `blizzard runner pause`, "runner-ceiling" for a spend-
    # ceiling crossing; `reason` is the composed ceiling+spend string, `None` for a manual
    # pause.
    locally_paused_by: str | None = None
    locally_paused_reason: str | None = None
    # The runner's configured environment-pool size — ``None`` when the registering client
    # reported none, never a fabricated zero.
    env_capacity: int | None = None
    # The runner's newest external-subscription-usage sample (issue #218) — absent when
    # the runner has never sampled one, or when the latest sample is older than the
    # hub's staleness threshold (`EXTERNAL_USAGE_STALE_AFTER`); never a fabricated
    # empty/zero value.
    external_subscription_usage: ExternalSubscriptionUsageView | None = None


class RunnerListResponse(BaseModel):
    """The fleet registry — every registered runner with its liveness."""

    runners: list[RunnerView] = []


class RunnerPauseRequest(BaseModel):
    """Set a runner's pause brake — records who flipped it."""

    by: str = "operator"
