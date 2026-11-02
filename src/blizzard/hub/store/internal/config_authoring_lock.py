"""Transaction entrance lock for config authoring decisions."""

from sqlalchemy.engine import Connection

from blizzard.hub.store.internal.chunk_rows import lock_keys


def lock_config_authoring(conn: Connection) -> None:
    lock_keys(conn, "config-authoring", ["config"])
