"""Record a takeover the way a test seeds one: inside a takeover-locked unit opened and committed for the call."""

from __future__ import annotations

from typing import Any

from blizzard.runner.lifecycle.takeover import IWriteTakeoverRepository


def record_takeover_in_unit(store: IWriteTakeoverRepository, **fields: Any) -> int | None:
    """``store.record_takeover(**fields)`` on a fresh takeover-locked unit, committed when the call returns."""
    with store.locked_takeover() as unit:
        return store.record_takeover(unit=unit, **fields)
