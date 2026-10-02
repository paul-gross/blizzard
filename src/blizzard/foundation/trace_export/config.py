from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class TracingConfig:
    """Resolved ``[tracing]`` config — the trace sweep's own knobs, each an integer
    of seconds or a count with a working default. Whether tracing is on at all is never
    decided here: OpenTelemetry's own environment variables own enablement."""

    sweep_seconds: int = 60
    #: How long a closed unit must have been closed before it is exported; 0 exports at once.
    settle_seconds: int = 300
    batch_limit: int = 200
    max_lag_seconds: int = 86400
    replay_max_window: int = 604800

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
        )

    @staticmethod
    def _integer(raw: Mapping[str, object], key: str, default: int, invalid: type[Exception], *, minimum: int) -> int:
        value = raw.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise invalid(f"tracing.{key} must be an integer, got {value!r}")
        if value < minimum:
            bound = "non-negative" if minimum == 0 else "positive"
            raise invalid(f"tracing.{key} must be {bound}, got {value!r}")
        return value

    def to_toml(self, *, unit: str) -> list[str]:
        """The ``[tracing]`` block. Every knob is rendered — commented out at its default,
        live once overridden — so the file shows an operator what the values ARE."""
        defaults = TracingConfig()
        lines = [
            "\n# Fleet-trace export knobs. Tracing itself turns on only through\n"
            "# OpenTelemetry's own variables (OTEL_EXPORTER_OTLP_TRACES_ENDPOINT or\n"
            "# OTEL_EXPORTER_OTLP_ENDPOINT); these tune the sweep once it runs. Seconds,\n"
            f"# except batch_limit ({unit} per export). Uncomment to override.\n",
            "[tracing]\n",
        ]
        for key in ("sweep_seconds", "settle_seconds", "batch_limit", "max_lag_seconds", "replay_max_window"):
            value = getattr(self, key)
            lines.append(f"# {key} = {value}\n" if value == getattr(defaults, key) else f"{key} = {value}\n")
        return lines
