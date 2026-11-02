"""Shared serialization key for queue position writers."""

from sqlalchemy import Connection

from blizzard.hub.store.internal.chunk_rows import lock_keys


def lock_queue_positions(conn: Connection) -> None:
    lock_keys(conn, "queue-positions", ["queue"])
