"""Shared exclusion for a routine and scope's baseline."""

import json
from collections.abc import Iterable

from sqlalchemy.engine import Connection

from blizzard.hub.store.internal.chunk_rows import lock_keys


def lock_finding_set_baselines(conn: Connection, pairs: Iterable[tuple[str, str, str]]) -> None:
    lock_keys(conn, "finding-set-baseline", [json.dumps(pair) for pair in pairs])
