"""Seeding for the project/repository position facts the bounded-reader component tests read."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import insert

from blizzard.hub.store.schema import project_repository_positions as positions
from tests.support.scaffolding import HubHarness, stamp

_AT = datetime(2026, 1, 1, tzinfo=UTC)


def record_fact(hub: HubHarness, project_id: str, repository_id: str, position: float | None) -> None:
    with hub.engine.begin() as conn:
        conn.execute(
            insert(positions).values(
                stamp(
                    positions,
                    project_id=project_id,
                    repository_id=repository_id,
                    position=position,
                    set_at=_AT,
                    set_by="test",
                )
            )
        )
