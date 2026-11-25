"""``stale_revision`` — the compare read a config apply's urgency guards share (component tier)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.store import batching as batching_module
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.urgency.declarations import NamedRevision
from blizzard.hub.domain.urgency.model import NO_MARK, Urgency, UrgencyId
from blizzard.hub.store.internal.urgency_store import UrgencyStore, stale_revision
from tests.support.scaffolding import hub_store_connections, migrated_hub_at, most_bound_per_statement

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_stale_revision_batches_its_read_and_names_the_first_moved_urgency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 2)
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migrated_hub_at(db_url)
    engine = create_engine_from_url(db_url)
    connections = hub_store_connections(engine)
    store = UrgencyStore(connections)
    standard, change = Urgency.standard(ChangeContext(actor="test", door=Door.API), at=_T0)
    ids = [UrgencyId(f"urg_{i}") for i in range(5)]
    for i, urgency_id in enumerate(ids):
        urgency = replace(standard, urgency_id=urgency_id, slug=f"u{i}")
        store.create(urgency, mark=NO_MARK, position=float(i + 10), change=replace(change, record_key=f"u{i}"))
    held = [NamedRevision(urgency_id, 1, f"u{i}") for i, urgency_id in enumerate(ids)]
    moved = [*held[:3], replace(held[3], revision=2), replace(held[4], revision=2)]
    answers: list[object] = []

    def read() -> None:
        with connections.read("stale_revision") as conn:
            answers.extend((stale_revision(conn, held), stale_revision(conn, moved)))

    assert most_bound_per_statement(engine, read, ids) == 2
    assert answers == [None, (moved[3], 1)]
