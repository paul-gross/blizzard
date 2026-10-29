"""The external-usage sampling pass — every subscription the host declares, sampled once per cadence on the host,
whichever runners spend it, off any runner's tick.

Directly callable (``bzh:steppable-loop``); ``runner host`` runs it on the host tick's own
:class:`~blizzard.runner.loop.periodic_pass_driver.PeriodicPassDriver`, so a sample blocked on the vendor's endpoint
never holds a runner's tick (``bzh:lane-contract`` clause 5)."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.subscription_miss import SampleMissReason
from blizzard.foundation.usage_windows import admit_usage_window
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.subscriptions.subscription_sampler import (
    ExternalSubscriptionUsageSnapshot,
    ISubscriptionSampler,
    sample_due,
)
from blizzard.runner.usage.repository import (
    IReadExternalUsageRepository,
    IWriteExternalUsageAttemptRepository,
    IWriteHostLaneAppendRepository,
    external_usage_attempt,
)

__all__ = [
    "ExternalUsageAttemptLane",
    "ExternalUsageSamplePass",
    "IAttemptLaneRunner",
    "IEarlySampleTriggers",
    "IExternalUsageAttemptSink",
    "IHostLaneStanding",
    "IRegistrationStanding",
    "IRunnerRegistration",
    "SampledSubscription",
]

_log = get_logger("blizzard.runner.subscriptions")


@dataclass(frozen=True)
class SampledSubscription:
    """One declared subscription with its resolved sampler, ``None`` when its provider binds none."""

    slug: str
    name: str
    sample_interval_seconds: int
    sampler: ISubscriptionSampler | None


class IExternalUsageAttemptSink(Protocol):
    """Where one sampling attempt lands — the host chooses the lane on each pass."""

    def record(
        self,
        *,
        slug: str,
        sampled_at: datetime,
        payload: str | None,
        report_kind: str,
        report_payload: str,
        miss_reason: str | None,
    ) -> None:
        """Record the attempt and buffer its report, atomically, on whichever lane the host is on."""
        ...


class IEarlySampleTriggers(Protocol):
    """Whether a subscription is due a sample ahead of its cadence — what its exhaustion asks of the pass."""

    def due_early(self, slug: str, *, last_attempt_at: datetime | None, now: datetime) -> bool:
        """``slug`` should be sampled now although its cadence has not come round, given its newest attempt (a miss
        included) ``last_attempt_at``. Each early sample fires once: the attempt it records is then the newest."""
        ...


class IRegistrationStanding(Protocol):
    """One reading of the host's registration: whether it holds a host token."""

    def registered(self) -> bool: ...


class IHostLaneStanding(Protocol):
    """The host registration's latest outcome, read afresh on every attempt."""

    def current(self) -> IRegistrationStanding: ...


class IRunnerRegistration(Protocol):
    """A runner's identity holder, read only for whether its identity row exists yet."""

    def current(self) -> object | None: ...


class IAttemptLaneRunner(Protocol):
    """One runner of the host, as the attempt lane reaches it: its identity, its runner-bound attempt handle, and
    its event publisher."""

    @property
    def identity(self) -> IRunnerRegistration: ...

    @property
    def external_usage_attempts(self) -> IWriteExternalUsageAttemptRepository: ...

    @property
    def events(self) -> IRunnerEventPublisher | None: ...


class ExternalUsageAttemptLane:
    """Where the host's sampling pass records an attempt: a registered host's own lane — one holding a host token,
    whether or not its hub has answered yet — else, an unregistered host having none, the attempt handle and event
    publisher of its first runner whose identity row exists, as a runner's own sample did: a runner-scoped row names a
    registered runner (``runner:rows-name-registered-runner``). With no such runner the attempt is not recorded, and
    the pass samples again on the next host tick, until a runner registers."""

    def __init__(
        self,
        host_lane: IWriteHostLaneAppendRepository,
        runners: Callable[[], Sequence[IAttemptLaneRunner]],
        *,
        registration: IHostLaneStanding,
    ) -> None:
        self._host_lane = host_lane
        self._runners = runners
        self._registration = registration

    def record(
        self,
        *,
        slug: str,
        sampled_at: datetime,
        payload: str | None,
        report_kind: str,
        report_payload: str,
        miss_reason: str | None,
    ) -> None:
        if self._registration.current().registered():
            self._host_lane.record_host_external_usage_attempt(
                slug=slug,
                sampled_at=sampled_at,
                payload=payload,
                report_kind=report_kind,
                report_payload=report_payload,
                miss_reason=miss_reason,
            )
            return
        reporter = next((runner for runner in self._runners() if runner.identity.current() is not None), None)
        if reporter is None:
            _log.warning("external usage attempt not recorded: no runner of the host has registered", slug=slug)
            return
        seq = reporter.external_usage_attempts.record_external_usage_attempt(
            slug=slug,
            sampled_at=sampled_at,
            payload=payload,
            report_kind=report_kind,
            report_payload=report_payload,
            miss_reason=miss_reason,
        )
        events = reporter.events
        if seq is not None and events is not None:
            events.publish_fact_changed(seq=seq, kind=report_kind, chunk_id=None, lease_id=None)


_attempt_lane_conforms: type[IExternalUsageAttemptSink] = ExternalUsageAttemptLane


class ExternalUsageSamplePass:
    """Per slug, in order: due check on the newest attempt — its cadence, or an early trigger — sample, record. A
    sampler a provider binds none of leaves the slug declared and unsampled, with no attempt row. One slug's failure
    never skips the next."""

    def __init__(
        self,
        *,
        subscriptions: Sequence[SampledSubscription],
        attempts: IReadExternalUsageRepository,
        early: IEarlySampleTriggers,
        sink: IExternalUsageAttemptSink,
        clock: IClock,
    ) -> None:
        self._subscriptions = tuple(subscriptions)
        self._attempts = attempts
        self._early = early
        self._sink = sink
        self._clock = clock

    def run(self) -> None:
        """One pass over every declared subscription that is due."""
        for subscription in self._subscriptions:
            try:
                self._sample_one(subscription)
            except Exception as exc:  # second line of defense — the sampler contract already promises this
                _log.warning("external subscription usage sample pass failed", slug=subscription.slug, detail=str(exc))

    def _sample_one(self, subscription: SampledSubscription) -> None:
        sampler = subscription.sampler
        if sampler is None:
            return
        anchor = self._attempts.last_external_usage_attempt_at(subscription.slug)
        now = self._clock.now()
        due = sample_due(anchor, now, subscription.sample_interval_seconds) or self._early.due_early(
            subscription.slug, last_attempt_at=anchor, now=now
        )
        if not due:
            return
        attempt = external_usage_attempt(sampler.sample(), slug=subscription.slug, at=self._clock.now())
        result = attempt.result
        if isinstance(result, SampleMissReason):
            payload = None
            report_payload = json.dumps(_miss_payload(subscription, result, missed_at=attempt.sampled_at))
        else:
            payload = report_payload = json.dumps(_sampled_payload(subscription, result))
        self._sink.record(
            slug=attempt.slug,
            sampled_at=attempt.sampled_at,
            payload=payload,
            report_kind=attempt.report_kind,
            report_payload=report_payload,
            miss_reason=attempt.miss_reason.value if attempt.miss_reason is not None else None,
        )


def _miss_payload(
    subscription: SampledSubscription, reason: SampleMissReason, *, missed_at: datetime
) -> dict[str, object]:
    """The stable JSON shape for a sampler miss — exactly ``{slug, name, missed_at, reason}``, the reason only: never
    a token, a refresh token, or a path crosses on a miss."""
    return {
        "slug": subscription.slug,
        "name": subscription.name,
        "missed_at": iso_utc(missed_at),
        "reason": reason.value,
    }


def _sampled_payload(
    subscription: SampledSubscription, snapshot: ExternalSubscriptionUsageSnapshot
) -> dict[str, object]:
    """The stable JSON shape for a sampled snapshot — both this attempt's stored ``payload`` and its buffered
    outbound report use this exact shape. ``slug`` and ``name`` name the declared subscription and its
    operator-facing label. A window the hub's own fact model would refuse is dropped here rather than sent, so the
    persisted attempt and the outbound report carry the same complete windows."""
    windows: list[dict[str, object]] = []
    for window in snapshot.windows:
        payload: dict[str, object] = {
            "window": window.window,
            "utilization_pct": window.utilization_pct,
            "resets_at": iso_utc(window.resets_at),
            "window_seconds": window.window_seconds,
        }
        admitted = admit_usage_window(payload)
        if isinstance(admitted, str):
            _log.warning(
                "dropped malformed external usage window", slug=subscription.slug, window=window.window, reason=admitted
            )
            continue
        windows.append(payload)
    return {
        "slug": subscription.slug,
        "name": subscription.name,
        "sampled_at": iso_utc(snapshot.sampled_at),
        "windows": windows,
    }
