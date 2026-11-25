"""Builders for the store adapters under ``internal/`` — a daemon's composition reaches them here
(``bzh:internal-visibility``)."""

from __future__ import annotations

from sqlalchemy import Engine

from blizzard.foundation.store.internal.store_status_reader import SqlAlchemyStoreStatusReader
from blizzard.foundation.store.status import IStoreStatusReader


def store_status_reader(engine: Engine) -> IStoreStatusReader:
    """Reads ``engine``'s reachability and applied revision."""
    return SqlAlchemyStoreStatusReader(engine)
