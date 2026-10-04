"""Claude Code's own telemetry name rules: which environment names configure a destination for a signal,
and which variables the binding sets to point a captured signal at the runner.

The public surface is :mod:`blizzard.runner.harness.harness_telemetry`; nothing outside ``harness/`` reads this."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from blizzard.foundation.platform_tracing.signals import TelemetrySignal
from blizzard.foundation.trace_export.settings import ENV_ENDPOINT
from blizzard.runner.harness.internal.harness_shared import receiver_env

#: Claude Code's undocumented detailed-beta tracing path: its own endpoint, live only with the second name.
BETA_TRACING_ENDPOINT = "BETA_TRACING_ENDPOINT"
ENABLE_BETA_TRACING_DETAILED = "ENABLE_BETA_TRACING_DETAILED"

ENABLE_TELEMETRY = "CLAUDE_CODE_ENABLE_TELEMETRY"
ENHANCED_TELEMETRY_BETA = "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA"
_FALSY = frozenset({"", "0", "false", "no", "off"})

#: The detailed-beta pair exports these two signals, and no others.
_BETA_SIGNALS = frozenset({TelemetrySignal.TRACES, TelemetrySignal.LOGS})


def _captured_env(signal: TelemetrySignal, local_api_url: str, lease_token: str) -> dict[str, str]:
    return {signal.exporter_variable: "otlp", **receiver_env(signal, local_api_url, lease_token)}


#: Names that point a worker at the runner's receiver, never an operator's destination; a signal's exporter is not one.
RUNNER_OWNED_NAMES = frozenset(
    {ENABLE_TELEMETRY, ENHANCED_TELEMETRY_BETA}
    | {name for signal in TelemetrySignal for name in receiver_env(signal, "", "")}
)


def operator_configured(signal: TelemetrySignal, env: Mapping[str, str]) -> bool:
    """Whether ``env`` already configures ``signal``'s export: its own exporter, endpoint, headers or protocol,
    the generic endpoint, or, for traces and logs, the detailed-beta pair. A settings ``env`` header or protocol
    would replace the runner's lease-token header or protocol, so the runner could not capture the signal. A
    falsy ``CLAUDE_CODE_ENABLE_TELEMETRY`` means the operator switched telemetry off, so every signal yields."""

    def present(name: str) -> bool:
        return bool(env.get(name, "").strip())

    if ENABLE_TELEMETRY in env and env[ENABLE_TELEMETRY].strip().lower() in _FALSY:
        return True
    own = (signal.exporter_variable, signal.endpoint_variable, signal.headers_variable, signal.protocol_variable)
    if any(present(name) for name in own) or present(ENV_ENDPOINT):
        return True
    return signal in _BETA_SIGNALS and present(BETA_TRACING_ENDPOINT) and present(ENABLE_BETA_TRACING_DETAILED)


def captured_env(signals: Iterable[TelemetrySignal], local_api_url: str, lease_token: str) -> dict[str, str]:
    """Each of ``signals`` pointed at the runner's receiver by signal-specific names only (a generic name would
    capture a signal configured with an exporter but no endpoint), plus both switches when any is captured."""
    chosen = tuple(signals)
    if not chosen:
        return {}
    env = {ENABLE_TELEMETRY: "1", ENHANCED_TELEMETRY_BETA: "1"}
    for signal in chosen:
        env.update(_captured_env(signal, local_api_url, lease_token))
    return env


__all__ = ["RUNNER_OWNED_NAMES", "captured_env", "operator_configured"]
