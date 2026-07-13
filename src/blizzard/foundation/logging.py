"""structlog wiring (``bzh:structlog-logging``), routed to **stderr**.

Diagnostics go to stderr so a daemon's stdout stays a clean surface; the renderer
is chosen by configuration, defaulting on TTY detection — a colored console for
interactive runs, a JSON renderer when an agent, a service, or CI consumes the
logs. One call-site convention regardless of renderer: pass structured fields as
key-value pairs, never interpolated into the message string.
"""

from __future__ import annotations

import os
import sys

import structlog

_configured = False

#: Override the renderer regardless of TTY. ``json`` / ``console`` (case-insensitive);
#: anything else falls through to TTY detection.
ENV_LOG_FORMAT = "BZ_LOG_FORMAT"


def _resolve_use_json(json_logs: bool | None) -> bool:
    """Pick the renderer: explicit arg > ``$BZ_LOG_FORMAT`` > TTY detection.

    The precedence realizes ``bzh:structlog-logging``'s "defaulting by TTY
    detection and overridable by config/env": an interactive run gets the console
    renderer, an agent/CI pipe gets JSON, and either can be forced by config
    (the ``json_logs`` argument) or the environment.
    """
    if json_logs is not None:
        return json_logs
    fmt = os.environ.get(ENV_LOG_FORMAT, "").strip().lower()
    if fmt == "json":
        return True
    if fmt in {"console", "text"}:
        return False
    return not sys.stderr.isatty()


def configure(*, json_logs: bool | None = None) -> None:
    """Configure structlog once. ``json_logs`` overrides the env and TTY defaults."""
    global _configured
    if _configured:
        return
    use_json = _resolve_use_json(json_logs)
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
