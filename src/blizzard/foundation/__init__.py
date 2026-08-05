"""The shared kernel both daemons compose.

Cross-cutting infrastructure only — the injected clock (``bzh:injected-clock``),
structlog wiring (``bzh:structlog-logging``), the portable SQLAlchemy engine
(``bzh:sql-portable``), the Alembic migration runner (``bzh:manual-migrations``),
and the web-app mount seam. No domain rules (``bzh:domain-core``)."""

from __future__ import annotations
