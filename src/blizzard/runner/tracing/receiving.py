"""The worker telemetry receiver's service: whether a receiver is on, what each export keeps,
whether the lease's rate admits it, and where the kept items go.

:mod:`~blizzard.runner.tracing.receiver` decides what one span, data point or log record keeps; this
orchestrates one export. The OTLP routes only authenticate the lease, bound and decode the body, and
map :class:`ReceiverOff` and :class:`RateExceeded` to their statuses."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.platform_tracing.handle import IPlatformTracing
from blizzard.foundation.platform_tracing.received import (
    ReceivedDataPoint,
    ReceivedLogRecord,
    ReceivedMetrics,
    ReceivedSpan,
)
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryNames
from blizzard.runner.hub.identity import ICurrentRunnerIdentity, RunnerIdentity
from blizzard.runner.leases import Lease
from blizzard.runner.tracing.received_export import IReceivedTelemetryExport
from blizzard.runner.tracing.receiver import (
    CLI_SERVICE_NAME,
    Admission,
    admit_data_points,
    admit_log_records,
    route_spans,
    service_name_for,
    spans_by_service_name,
)
from blizzard.runner.tracing.receiver_limits import ReceiverBounds, ReceiverCounter, SpanRateLimiter

__all__ = ["RateExceeded", "ReceiverOff", "TelemetryReceiver"]


class ReceiverOff(Exception):
    """The receiver asked is switched off — platform tracing, or ``harness_telemetry`` for
    metrics and logs — or the runner has not registered with its hub yet, so nothing it keeps
    could carry its id. ``detail`` names which."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class RateExceeded(Exception):
    """The items an export keeps exceed the lease's rate; nothing of it was forwarded."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class TelemetryReceiver:
    """One worker telemetry export's orchestration, over the process's limiters and tallies.
    ``clock`` is ``None`` where the composer wired none; each limiter then reads its own. ``identity``
    answers who every kept item says sent it: the runner's latest registration."""

    platform_tracing: IPlatformTracing
    received_telemetry: IReceivedTelemetryExport
    span_limiter: SpanRateLimiter
    span_counter: ReceiverCounter
    harness_span_counter: ReceiverCounter
    telemetry_names: tuple[HarnessTelemetryNames, ...]
    metric_bounds: ReceiverBounds
    log_bounds: ReceiverBounds
    clock: IClock | None
    identity: ICurrentRunnerIdentity
    worker_programs: bool = False
    harness_telemetry: bool = False
    mapped_services: Mapping[str, str] = field(default_factory=dict)

    def require_traces(self) -> None:
        """Refuse while platform tracing is off, or before the runner's first registration."""
        if not self.platform_tracing.enabled:
            raise ReceiverOff("platform tracing is off")
        self._runner()

    def require_harness_telemetry(self) -> None:
        """Refuse unless platform tracing and ``harness_telemetry`` are both on, or before the runner's
        first registration."""
        if not (self.platform_tracing.enabled and self.harness_telemetry):
            raise ReceiverOff("harness telemetry is off")
        self._runner()

    def receive_spans(self, lease: Lease, spans: list[ReceivedSpan]) -> int:
        """Route, charge, forward and count one trace export; return how many spans it refused.
        Raises :class:`RateExceeded` — counting every span as dropped — when the kept spans
        exceed the lease's span rate."""
        routing = route_spans(
            spans,
            lease,
            runner=self._runner(),
            programs=self.worker_programs,
            harness=self.harness_telemetry,
            names=self.telemetry_names,
        )
        if routing.kept and not self.span_limiter.take(lease.lease_id, routing.kept, now=self._now()):
            self.span_counter.record(accepted=0, dropped=routing.rest_received)
            self.harness_span_counter.record(accepted=0, dropped=routing.harness_received)
            raise RateExceeded("span rate exceeded")
        grouped = spans_by_service_name(
            [*routing.harness.kept, *routing.others], self.mapped_services, self.telemetry_names
        )
        for service_name, group in grouped.items():
            self.platform_tracing.forward(group, service_name)
        self.platform_tracing.forward(routing.cli, CLI_SERVICE_NAME)
        self.harness_span_counter.record(accepted=len(routing.harness.kept), dropped=routing.harness.dropped)
        self.span_counter.record(accepted=routing.accepted, dropped=routing.dropped)
        return routing.refused

    def receive_metrics(self, lease: Lease, decoded: ReceivedMetrics) -> int:
        """Keep the bindings' metrics scopes (a summary point is refused), charge, forward, count;
        return how many data points it refused."""
        scopes = frozenset(n.metrics_scope for n in self.telemetry_names)
        admitted = admit_data_points(decoded.points, lease, scopes, runner=self._runner())
        admission = Admission(kept=admitted.kept, dropped=admitted.dropped + decoded.unsupported)
        self._forward(
            self.metric_bounds,
            lease,
            admission,
            received=len(decoded.points) + decoded.unsupported,
            forward=self.received_telemetry.forward_metrics,
            scope_of=lambda point: point.scope_name,
        )
        return admission.dropped

    def receive_logs(self, lease: Lease, records: list[ReceivedLogRecord]) -> int:
        """Keep the bindings' logs scopes, charge, forward, count; return how many log records it refused."""
        scopes = frozenset(n.logs_scope for n in self.telemetry_names)
        admission = admit_log_records(records, lease, scopes, runner=self._runner())
        self._forward(
            self.log_bounds,
            lease,
            admission,
            received=len(records),
            forward=self.received_telemetry.forward_logs,
            scope_of=lambda record: record.scope_name,
        )
        return admission.dropped

    def _now(self) -> datetime | None:
        return self.clock.now() if self.clock is not None else None

    def _runner(self) -> RunnerIdentity:
        runner = self.identity.current()
        if runner is None:
            raise ReceiverOff("the runner has not registered with its hub yet")
        return runner

    def _forward[T: (ReceivedDataPoint, ReceivedLogRecord)](
        self,
        bounds: ReceiverBounds,
        lease: Lease,
        admission: Admission[T],
        *,
        received: int,
        forward: Callable[[Sequence[T], str], None],
        scope_of: Callable[[T], str],
    ) -> None:
        if admission.kept and not bounds.limiter.take(lease.lease_id, len(admission.kept), now=self._now()):
            bounds.counter.record(accepted=0, dropped=received)
            raise RateExceeded("rate exceeded")
        groups: dict[str, list[T]] = {}
        for item in admission.kept:
            groups.setdefault(service_name_for(scope_of(item), self.mapped_services, self.telemetry_names), []).append(
                item
            )
        for service_name, group in groups.items():
            forward(group, service_name)
        bounds.counter.record(accepted=len(admission.kept), dropped=admission.dropped)
