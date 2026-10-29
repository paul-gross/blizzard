from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.config_keys import BOOLEAN, INTEGER, NUMBER, TABLE, ConfigKey, ConfigView
from blizzard.foundation.roles import domain_model


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

    @classmethod
    def of(cls, tracing: ConfigView, invalid: type[Exception]) -> TracingConfig:
        """The ``[tracing]`` table, read through ``tracing`` — the view at its path."""
        defaults = cls()
        return cls(
            sweep_seconds=cls._integer(tracing, "sweep_seconds", defaults.sweep_seconds, invalid, minimum=1),
            settle_seconds=cls._integer(tracing, "settle_seconds", defaults.settle_seconds, invalid, minimum=0),
            batch_limit=cls._integer(tracing, "batch_limit", defaults.batch_limit, invalid, minimum=1),
            max_lag_seconds=cls._integer(tracing, "max_lag_seconds", defaults.max_lag_seconds, invalid, minimum=1),
            replay_max_window=cls._integer(
                tracing, "replay_max_window", defaults.replay_max_window, invalid, minimum=1
            ),
            platform=cls._boolean(tracing, "platform", defaults.platform, invalid),
            platform_sample_ratio=cls._ratio(tracing, "platform_sample_ratio", defaults.platform_sample_ratio, invalid),
        )

    @staticmethod
    def _boolean(tracing: ConfigView, key: str, default: bool, invalid: type[Exception]) -> bool:
        value = tracing.get(key, default)
        if not isinstance(value, bool):
            raise invalid(f"tracing.{key} must be true or false, got {value!r}")
        return value

    @staticmethod
    def _ratio(tracing: ConfigView, key: str, default: float, invalid: type[Exception]) -> float:
        value = tracing.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise invalid(f"tracing.{key} must be a number, got {value!r}")
        if not 0 <= value <= 1:
            raise invalid(f"tracing.{key} must lie in [0, 1], got {value!r}")
        return float(value)

    @staticmethod
    def _integer(tracing: ConfigView, key: str, default: int, invalid: type[Exception], *, minimum: int) -> int:
        value = tracing.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise invalid(f"tracing.{key} must be an integer, got {value!r}")
        if value < minimum:
            bound = "non-negative" if minimum == 0 else "positive"
            raise invalid(f"tracing.{key} must be {bound}, got {value!r}")
        return value

    def to_toml(self, *, unit: str) -> list[str]:
        """The ``[tracing]`` block. Every knob is rendered — commented out at its default,
        live once overridden — so the file shows an operator what the values ARE."""
        return [self.banner(unit), "[tracing]\n", *self.knob_lines(SHARED_KEYS)]

    @staticmethod
    def banner(unit: str, notes: str = "") -> str:
        """The comment block above ``[tracing]``; ``notes`` is extra lines appended after the shared keys."""
        return (
            "\n# Trace knobs. Tracing turns on only through OpenTelemetry's own variables\n"
            "# (OTEL_EXPORTER_OTLP_TRACES_ENDPOINT or OTEL_EXPORTER_OTLP_ENDPOINT); the\n"
            "# sweep knobs tune fleet-trace export once it runs. Seconds, except batch_limit\n"
            f"# ({unit} per export). platform = true also emits platform spans (requests,\n"
            "# queries, outbound calls), roots kept at platform_sample_ratio, 0 to 1.\n"
            f"{notes}"
            "# Uncomment to override.\n"
        )

    def knob_lines(self, keys: tuple[str, ...]) -> list[str]:
        """``keys`` rendered one line each: commented out at the default, live once overridden."""
        defaults = type(self)()
        lines: list[str] = []
        for key in keys:
            value = getattr(self, key)
            literal = toml_literal(value)
            lines.append(f"# {key} = {literal}\n" if value == getattr(defaults, key) else f"{key} = {literal}\n")
        return lines


SHARED_KEYS = (
    "sweep_seconds",
    "settle_seconds",
    "batch_limit",
    "max_lag_seconds",
    "replay_max_window",
    "platform",
    "platform_sample_ratio",
)


_DEFAULTS = TracingConfig()

#: Every ``[tracing]`` key of the shared config, declared beside the parser that reads it.
TRACING_KEYS: tuple[ConfigKey, ...] = (
    ConfigKey("tracing", TABLE),
    ConfigKey("tracing.sweep_seconds", INTEGER, default=_DEFAULTS.sweep_seconds),
    ConfigKey("tracing.settle_seconds", INTEGER, default=_DEFAULTS.settle_seconds),
    ConfigKey("tracing.batch_limit", INTEGER, default=_DEFAULTS.batch_limit),
    ConfigKey("tracing.max_lag_seconds", INTEGER, default=_DEFAULTS.max_lag_seconds),
    ConfigKey("tracing.replay_max_window", INTEGER, default=_DEFAULTS.replay_max_window),
    ConfigKey("tracing.platform", BOOLEAN, default=_DEFAULTS.platform),
    ConfigKey("tracing.platform_sample_ratio", NUMBER, default=_DEFAULTS.platform_sample_ratio),
)


def toml_literal(value: object) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)
