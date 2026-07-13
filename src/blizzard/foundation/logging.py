"""structlog wiring (``bzh:structlog-logging``), routed to **stderr**.

Diagnostics go to stderr so a daemon's stdout stays a clean surface; the renderer
is chosen by configuration, defaulting on TTY detection — a colored console for
interactive runs, a JSON renderer when an agent, a service, or CI consumes the
logs. One call-site convention regardless of renderer: pass structured fields as
key-value pairs, never interpolated into the message string.
"""

from __future__ import annotations

import sys

import structlog

_configured = False


def configure(*, json_logs: bool | None = None) -> None:
    """Configure structlog once. ``json_logs`` overrides the TTY default."""
    global _configured
    if _configured:
        return
    use_json = (not sys.stderr.isatty()) if json_logs is None else json_logs
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if use_json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to ``name``, configuring once."""
    configure()
    return structlog.get_logger(name)
