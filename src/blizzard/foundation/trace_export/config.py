from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from blizzard.foundation.cli_spans import SERVICE_NAME as CLI_SERVICE_NAME
from blizzard.foundation.platform_tracing.attributes import CLI_SCOPE
from blizzard.foundation.roles import domain_model

RESERVED_SERVICE_NAMES = frozenset({"blizzard-hub", "blizzard-runner", "blizzard-chunk", CLI_SERVICE_NAME})


@domain_model
@dataclass(frozen=True)
class TracingConfig:
    """Resolved ``[tracing]`` config — the trace sweep's own knobs, each an integer
    of seconds or a count with a working default — plus the platform-span switch and its root
    sample ratio. An OTLP endpoint in OpenTelemetry's own environment variables is the other switch."""

    sweep_seconds: int = 60
    #: How long a closed unit must have been closed before it is exported; 0 exports at once.
    settle_seconds: int = 300
    batch_limit: int = 200
    max_lag_seconds: int = 86400
    replay_max_window: int = 604800
    #: Platform spans run only with this on *and* an OTLP endpoint configured.
    platform: bool = False
    platform_sample_ratio: float = 0.01
    #: Worker programs' own spans reach the runner's receiver; takes effect only alongside ``platform``.
    worker_programs: bool = False
    #: Scope name to the ``service.name`` its kept worker-program or Claude Code telemetry leaves with.
    worker_program_services: Mapping[str, str] = field(default_factory=dict)
    #: Claude Code workers export all three signals to the runner's receivers; needs ``platform``.
    harness_telemetry: bool = False

    @classmethod
    def of(cls, raw_tracing: object, invalid: type[Exception]) -> TracingConfig:
        if not isinstance(raw_tracing, dict):
            return cls()
        defaults = cls()
        return cls(
            sweep_seconds=cls._integer(raw_tracing, "sweep_seconds", defaults.sweep_seconds, invalid, minimum=1),
            settle_seconds=cls._integer(raw_tracing, "settle_seconds", defaults.settle_seconds, invalid, minimum=0),
            batch_limit=cls._integer(raw_tracing, "batch_limit", defaults.batch_limit, invalid, minimum=1),
            max_lag_seconds=cls._integer(raw_tracing, "max_lag_seconds", defaults.max_lag_seconds, invalid, minimum=1),
            replay_max_window=cls._integer(
                raw_tracing, "replay_max_window", defaults.replay_max_window, invalid, minimum=1
            ),
            platform=cls._boolean(raw_tracing, "platform", defaults.platform, invalid),
            platform_sample_ratio=cls._ratio(
                raw_tracing, "platform_sample_ratio", defaults.platform_sample_ratio, invalid
            ),
            worker_programs=cls._boolean(raw_tracing, "worker_programs", defaults.worker_programs, invalid),
            worker_program_services=cls._services(raw_tracing, invalid),
            harness_telemetry=cls._boolean(raw_tracing, "harness_telemetry", defaults.harness_telemetry, invalid),
        )

    @staticmethod
    def _services(raw: Mapping[str, object], invalid: type[Exception]) -> dict[str, str]:
        table = raw.get("worker_program_services", {})
        if not isinstance(table, dict):
            raise invalid(f"tracing.worker_program_services must be a table, got {table!r}")
        for scope, name in table.items():
            entry = f"tracing.worker_program_services.{scope}"
            if not scope.strip():
                raise invalid("tracing.worker_program_services has an empty scope name")
            if scope == CLI_SCOPE:
                raise invalid(f"{entry} maps the CLI scope, whose service.name is fixed")
            if not isinstance(name, str) or not name.strip():
                raise invalid(f"{entry} must be a non-empty string, got {name!r}")
            if name in RESERVED_SERVICE_NAMES:
                raise invalid(f"{entry} must not be the reserved service name {name!r}")
        return dict(table)

    @staticmethod
    def _boolean(raw: Mapping[str, object], key: str, default: bool, invalid: type[Exception]) -> bool:
        value = raw.get(key, default)
        if not isinstance(value, bool):
            raise invalid(f"tracing.{key} must be true or false, got {value!r}")
        return value

    @staticmethod
    def _ratio(raw: Mapping[str, object], key: str, default: float, invalid: type[Exception]) -> float:
        value = raw.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise invalid(f"tracing.{key} must be a number, got {value!r}")
        if not 0 <= value <= 1:
            raise invalid(f"tracing.{key} must lie in [0, 1], got {value!r}")
        return float(value)

    @staticmethod
    def _integer(raw: Mapping[str, object], key: str, default: int, invalid: type[Exception], *, minimum: int) -> int:
        value = raw.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise invalid(f"tracing.{key} must be an integer, got {value!r}")
        if value < minimum:
            bound = "non-negative" if minimum == 0 else "positive"
            raise invalid(f"tracing.{key} must be {bound}, got {value!r}")
        return value

    def to_toml(self, *, unit: str, receiver: bool = False) -> list[str]:
        """The ``[tracing]`` block. Every knob is rendered — commented out at its default,
        live once overridden — so the file shows an operator what the values ARE."""
        defaults = TracingConfig()
        lines = [
            "\n# Trace knobs. Tracing turns on only through OpenTelemetry's own variables\n"
            "# (OTEL_EXPORTER_OTLP_TRACES_ENDPOINT or OTEL_EXPORTER_OTLP_ENDPOINT); the\n"
            "# sweep knobs tune fleet-trace export once it runs. Seconds, except batch_limit\n"
            f"# ({unit} per export). platform = true also emits platform spans (requests,\n"
            "# queries, outbound calls), roots kept at platform_sample_ratio, 0 to 1.\n"
            "# worker_programs = true (with platform) also lets a worker's own programs send\n"
            "# spans to the runner; a third-party program may record bodies or parameters.\n"
            "# harness_telemetry = true (with platform) points a Claude Code worker's metrics, logs\n"
            "# and traces at the runner for each signal you have not configured; its identity\n"
            "# attributes (user.email, organization.id) are exported with them.\n"
            f"{_SERVICES_NOTE if receiver else ''}"
            "# Uncomment to override.\n",
            "[tracing]\n",
        ]
        for key in _KEYS:
            value = getattr(self, key)
            literal = toml_literal(value)
            lines.append(f"# {key} = {literal}\n" if value == getattr(defaults, key) else f"{key} = {literal}\n")
        if self.worker_program_services:
            lines.append("\n[tracing.worker_program_services]\n")
            lines.extend(
                f"{json.dumps(scope)} = {json.dumps(name)}\n" for scope, name in self.worker_program_services.items()
            )
        elif receiver:
            lines.append('\n# [tracing.worker_program_services]\n# winter_cli = "winter-blizzard"\n')
        return lines


_SERVICES_NOTE = "# [tracing.worker_program_services] names that telemetry's service.name by scope.\n"

_KEYS = (
    "sweep_seconds",
    "settle_seconds",
    "batch_limit",
    "max_lag_seconds",
    "replay_max_window",
    "platform",
    "platform_sample_ratio",
    "worker_programs",
    "harness_telemetry",
)


def toml_literal(value: object) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)
