"""What happens to one of a harness binding's telemetry signals."""

from __future__ import annotations

from enum import StrEnum


class HarnessTelemetryOutcome(StrEnum):
    """What the runner does with one of a harness binding's telemetry signals."""

    OFF = "off"
    CAPTURED = "captured"
    OPERATOR_CONFIGURED = "operator_configured"
    NO_RUNNER_DESTINATION = "no_runner_destination"
