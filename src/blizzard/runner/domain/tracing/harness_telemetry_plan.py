"""The per-signal plan for Claude Code's own telemetry, and the names its telemetry carries once received.

Pure value types and constants: the plan is derived at the composition root
(:mod:`blizzard.runner.harness.claude_code.telemetry_plan`) and read from here."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.harness_telemetry_outcome import HarnessTelemetryOutcome
from blizzard.foundation.platform_tracing.signals import TelemetrySignal
from blizzard.foundation.roles import domain_model

CLAUDE_CODE_METRICS_SCOPE = "com.anthropic.claude_code"
CLAUDE_CODE_LOGS_SCOPE = "com.anthropic.claude_code.events"
CLAUDE_CODE_TRACING_SCOPE = "com.anthropic.claude_code.tracing"
CLAUDE_CODE_SCOPES = frozenset({CLAUDE_CODE_METRICS_SCOPE, CLAUDE_CODE_LOGS_SCOPE, CLAUDE_CODE_TRACING_SCOPE})

#: ``service.name`` on Claude Code's telemetry unless ``worker_program_services`` maps its scope.
CLAUDE_CODE_SERVICE_NAME = "blizzard-claude-code"


@domain_model
@dataclass(frozen=True)
class HarnessTelemetryPlan:
    """One outcome per signal: what the Claude Code binding does with that signal's exporter."""

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
