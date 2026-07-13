"""The shared kernel both daemons compose.

`foundation` holds the cross-cutting infrastructure the hub and the runner both
depend on — the injected clock (``bzh:injected-clock``), structlog wiring
(``bzh:structlog-logging``), the portable SQLAlchemy engine (``bzh:sql-portable``),
the Alembic migration runner + revision-mismatch guard (``bzh:manual-migrations``),
and the web-app mount seam. It carries **no domain rules** — those live in each
daemon's own ``domain`` layer (``bzh:domain-core``).
"""

from __future__ import annotations
