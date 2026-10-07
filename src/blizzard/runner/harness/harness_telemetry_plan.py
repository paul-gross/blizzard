"""The per-signal plan for a binding's own telemetry, and the names that telemetry carries once received.

Pure value types: each binding answers its plan and its names through its declaration
(:class:`~blizzard.runner.harness.declaration.IHarnessDeclaration`); the runner combines and reads them from here."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from blizzard.foundation.harness_telemetry_outcome import HarnessTelemetryOutcome
from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.telemetry_signals import TelemetrySignal

#: Engagement order, most engaged first: the outcome that wins when several bindings answer one signal.
_ENGAGEMENT = (
    HarnessTelemetryOutcome.CAPTURED,
    HarnessTelemetryOutcome.OPERATOR_CONFIGURED,
    HarnessTelemetryOutcome.NO_RUNNER_DESTINATION,
    HarnessTelemetryOutcome.OFF,
)


@domain_model
@dataclass(frozen=True)
class HarnessTelemetryNames:
    """What one binding's telemetry arrives under: the instrumentation scope of each signal, and the default
    ``service.name`` it leaves the runner with unless ``worker_program_services`` maps its scope."""

    traces_scope: str
    metrics_scope: str
    logs_scope: str
    service_name: str

    @property
    def scopes(self) -> frozenset[str]:
        return frozenset({self.traces_scope, self.metrics_scope, self.logs_scope})


@domain_model
@dataclass(frozen=True)
class HarnessTelemetryPlan:
    """One outcome per signal: what a binding does with that signal's exporter."""

    traces: HarnessTelemetryOutcome = HarnessTelemetryOutcome.OFF
    metrics: HarnessTelemetryOutcome = HarnessTelemetryOutcome.OFF
    logs: HarnessTelemetryOutcome = HarnessTelemetryOutcome.OFF

    def outcome(self, signal: TelemetrySignal) -> HarnessTelemetryOutcome:
        return {
            TelemetrySignal.TRACES: self.traces,
            TelemetrySignal.METRICS: self.metrics,
            TelemetrySignal.LOGS: self.logs,
        }[signal]

    def captured(self) -> tuple[TelemetrySignal, ...]:
        return tuple(s for s in TelemetrySignal if self.outcome(s) is HarnessTelemetryOutcome.CAPTURED)

    @classmethod
    def combine(cls, plans: Iterable[HarnessTelemetryPlan]) -> HarnessTelemetryPlan:
        """One plan over several bindings' plans: per signal, the most engaged outcome wins, so an all-off
        binding leaves the combination unchanged."""
        gathered = tuple(plans)

        def most_engaged(signal: TelemetrySignal) -> HarnessTelemetryOutcome:
            outcomes = {plan.outcome(signal) for plan in gathered}
            return next((o for o in _ENGAGEMENT if o in outcomes), HarnessTelemetryOutcome.OFF)

        return cls(
            traces=most_engaged(TelemetrySignal.TRACES),
            metrics=most_engaged(TelemetrySignal.METRICS),
            logs=most_engaged(TelemetrySignal.LOGS),
        )
