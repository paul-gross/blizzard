"""SQLAlchemy adapter for the forge annotation memory (package-private).

Append-only (``bzh:facts-not-status``): a source's newest ``forge_annotation_facts`` row says
whether this hub still counts it as annotated, so the memory outlives a restart."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from blizzard.hub.domain.observability.forge_status import IWriteAnnotatedSources
from blizzard.hub.store.internal.newest_fact import newest_fact_select
from blizzard.hub.store.schema import forge_annotation_facts
from blizzard.hub.store.scoped import ScopedConnections


class ForgeAnnotationStore:
    """Read and append adapter over the forge annotation memory."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def annotated_sources(self) -> frozenset[str]:
        table = forge_annotation_facts
        query = newest_fact_select(self._store, table, table.c.work_source_id, None, table.c.work_source_id).where(
            table.c.annotating
        )
        with self._store.read("annotated_sources") as conn:
            return frozenset(conn.execute(query).scalars().all())

    def record_annotated_sources(self, *, entered: Sequence[str], left: Sequence[str], at: datetime) -> None:
        rows = [{"work_source_id": key, "annotating": True, "recorded_at": at} for key in entered]
        rows += [{"work_source_id": key, "annotating": False, "recorded_at": at} for key in left]
        if not rows:
            return
        with self._store.write("record_annotated_sources") as conn:
            conn.execute(self._store.insert(forge_annotation_facts), rows)


_forge_annotation_store_conforms: type[IWriteAnnotatedSources] = ForgeAnnotationStore
