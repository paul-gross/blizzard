"""The portable SQLAlchemy engine factory (``bzh:sql-portable``).

Both stores run on sqlite (the fast local default and what tests use) or postgres,
selected only by the configured URL — the schema stays inside SQLAlchemy's
portable surface, so postgres is a URL change, not a second test matrix.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine


def create_engine_from_url(url: str) -> Engine:
    """Build an engine from a store URL, applying the sqlite-safe connect args."""
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        # The daemon serves the store from multiple request threads; sqlite's
        # default same-thread check would reject that. WAL and the single-writer
        # discipline are the daemon's concern.
        connect_args["check_same_thread"] = False
    return create_engine(url, future=True, connect_args=connect_args)
