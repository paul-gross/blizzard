"""structlog wiring (``bzh:structlog-logging``), routed to **stderr**.

Diagnostics go to stderr so a daemon's stdout stays a clean surface."""

from __future__ import annotations

import os
import sys

import structlog

_configured = False

#: Override the renderer regardless of TTY. ``json`` / ``console`` (case-insensitive);
#: anything else falls through to TTY detection.
ENV_LOG_FORMAT = "BZ_LOG_FORMAT"


class LogFormat:
    """The chosen renderer; the processor chain around it is the same either way."""

    @classmethod
    def of(cls, json_logs: bool | None) -> LogFormat:
        """Pick the renderer: explicit arg > ``$BZ_LOG_FORMAT`` > TTY detection."""
        if json_logs is not None:
            return Json() if json_logs else Console()
        fmt = os.environ.get(ENV_LOG_FORMAT, "").strip().lower()
        if fmt == "json":
            return Json()
        if fmt in {"console", "text"}:
            return Console()
        return Json() if not sys.stderr.isatty() else Console()

    def renderer(self) -> structlog.types.Processor:
        raise NotImplementedError

    def apply(self) -> None:
        structlog.configure(
            processors=[
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                self.renderer(),
            ],
            logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
            cache_logger_on_first_use=True,
        )


class Json(LogFormat):
    def renderer(self) -> structlog.types.Processor:
        return structlog.processors.JSONRenderer()


class Console(LogFormat):
    def renderer(self) -> structlog.types.Processor:
        return structlog.dev.ConsoleRenderer()


def configure(*, json_logs: bool | None = None) -> None:
    """Configure structlog once. ``json_logs`` overrides the env and TTY defaults."""
    global _configured
    if _configured:
        return
    LogFormat.of(json_logs).apply()
    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to ``name``, configuring once."""
    configure()
    return structlog.get_logger(name)
