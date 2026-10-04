"""What the runner does with one of Claude Code's telemetry signals — the vocabulary the runner's plan and its
wire status share."""

from __future__ import annotations

from enum import StrEnum


class HarnessTelemetryOutcome(StrEnum):
    """What the runner does with one of Claude Code's telemetry signals."""

    OFF = "off"
    CAPTURED = "captured"
    OPERATOR_CONFIGURED = "operator_configured"
    NO_RUNNER_DESTINATION = "no_runner_destination"
