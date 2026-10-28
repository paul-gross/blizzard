"""The runner's ``[tracing]`` config: the knobs every daemon shares, plus the three only the runner acts on — the
receiver's worker-program and harness-telemetry switches and the service names its kept telemetry leaves with."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, fields

from blizzard.foundation.cli_spans import SCOPE_NAME
from blizzard.foundation.cli_spans import SERVICE_NAME as CLI_SERVICE_NAME
from blizzard.foundation.config_keys import BOOLEAN, OPEN_TABLE, ConfigKey, ConfigView
from blizzard.foundation.roles import domain_model
from blizzard.foundation.trace_export.config import SHARED_KEYS, TracingConfig
from blizzard.foundation.untyped import as_table

RESERVED_SERVICE_NAMES = frozenset({"blizzard-hub", "blizzard-runner", "blizzard-chunk", CLI_SERVICE_NAME})

_SERVICES_NOTE = "# [tracing.worker_program_services] names that telemetry's service.name by scope.\n"

_NOTES = (
    "# worker_programs = true (with platform) also lets a worker's own programs send\n"
    "# spans to the runner; a third-party program may record bodies or parameters.\n"
    "# harness_telemetry = true (with platform) points a Claude Code worker's metrics, logs\n"
    "# and traces at the runner for each signal you have not configured; its identity\n"
    "# attributes (user.email, organization.id) are exported with them.\n"
    f"{_SERVICES_NOTE}"
)

_RUNNER_KEYS = ("worker_programs", "harness_telemetry")


@domain_model
@dataclass(frozen=True)
class RunnerTracingConfig(TracingConfig):
    """The shared ``[tracing]`` config plus the keys the runner's receiver reads."""

    #: Worker programs' own spans reach the runner's receiver; takes effect only alongside ``platform``.
    worker_programs: bool = False
    #: Scope name to the ``service.name`` its kept worker-program or Claude Code telemetry leaves with.
    worker_program_services: Mapping[str, str] = field(default_factory=dict[str, str])
    #: Claude Code workers export all three signals to the runner's receivers; needs ``platform``.
    harness_telemetry: bool = False

    @classmethod
    def of(cls, tracing: ConfigView, invalid: type[Exception]) -> RunnerTracingConfig:
        """The ``[tracing]`` table, read through ``tracing`` — the view at its path."""
        shared = TracingConfig.of(tracing, invalid)
        defaults = cls()
        return cls(
            **{f.name: getattr(shared, f.name) for f in fields(TracingConfig)},
            worker_programs=cls._boolean(tracing, "worker_programs", defaults.worker_programs, invalid),
            worker_program_services=cls._services(tracing, invalid),
            harness_telemetry=cls._boolean(tracing, "harness_telemetry", defaults.harness_telemetry, invalid),
        )

    @staticmethod
    def _services(tracing: ConfigView, invalid: type[Exception]) -> dict[str, str]:
        table = tracing.get("worker_program_services", {})
        if as_table(table) is None:
            raise invalid(f"tracing.worker_program_services must be a table, got {table!r}")
        services: dict[str, str] = {}
        for scope, name in tracing.table("worker_program_services").items():
            entry = f"tracing.worker_program_services.{scope}"
            if not scope.strip():
                raise invalid("tracing.worker_program_services has an empty scope name")
            if scope == SCOPE_NAME:
                raise invalid(f"{entry} maps the CLI scope, whose service.name is fixed")
            if not isinstance(name, str) or not name.strip():
                raise invalid(f"{entry} must be a non-empty string, got {name!r}")
            if name in RESERVED_SERVICE_NAMES:
                raise invalid(f"{entry} must not be the reserved service name {name!r}")
            services[scope] = name
        return services

    def to_toml(self, *, unit: str) -> list[str]:
        """The ``[tracing]`` block with the runner's keys and its services table after the shared ones."""
        lines = [self.banner(unit, _NOTES), "[tracing]\n", *self.knob_lines((*SHARED_KEYS, *_RUNNER_KEYS))]
        if self.worker_program_services:
            lines.append("\n[tracing.worker_program_services]\n")
            lines.extend(
                f"{json.dumps(scope)} = {json.dumps(name)}\n" for scope, name in self.worker_program_services.items()
            )
        else:
            lines.append('\n# [tracing.worker_program_services]\n# winter_cli = "winter-blizzard"\n')
        return lines


_DEFAULTS = RunnerTracingConfig()

#: The ``[tracing]`` keys only the runner reads, declared here beside the parser that reads them.
RUNNER_TRACING_KEYS: tuple[ConfigKey, ...] = (
    ConfigKey("tracing.worker_programs", BOOLEAN, default=_DEFAULTS.worker_programs),
    ConfigKey("tracing.worker_program_services", OPEN_TABLE),
    ConfigKey("tracing.harness_telemetry", BOOLEAN, default=_DEFAULTS.harness_telemetry),
)
