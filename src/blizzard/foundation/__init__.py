"""The shared kernel both daemons compose.

Cross-cutting infrastructure — the injected clock, structlog wiring, the portable
SQLAlchemy engine, the Alembic migration runner, the web-app mount seam — plus the
vocabulary both daemons speak. The *rules* stay in each daemon's domain (``bzh:domain-core``)."""

from __future__ import annotations
