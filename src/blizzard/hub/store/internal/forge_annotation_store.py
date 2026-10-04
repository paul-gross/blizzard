"""SQLAlchemy adapter for the forge annotation memory (package-private).

Append-only (``bzh:facts-not-status``): a source's newest ``forge_annotation_facts`` row says
whether this hub still counts it as annotated, so the memory outlives a restart."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, insert, select

from blizzard.hub.domain.observability.forge_status import IAnnotatedSources
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.schema import forge_annotation_facts


class ForgeAnnotationStore:
    """Read and append adapter over the forge annotation memory."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def annotated_sources(self) -> frozenset[str]:
        table = forge_annotation_facts
        newest = select(func.max(table.c.id).label("id")).group_by(table.c.source_name).subquery()
        query = select(table.c.source_name).join(newest, table.c.id == newest.c.id).where(table.c.annotating)
        with self._store.read("annotated_sources") as conn:
            return frozenset(conn.execute(query).scalars().all())

    def record_annotated_sources(self, *, entered: Sequence[str], left: Sequence[str], at: datetime) -> None:
        rows = [{"source_name": name, "annotating": True, "recorded_at": at} for name in entered]
        rows += [{"source_name": name, "annotating": False, "recorded_at": at} for name in left]
        if not rows:
            return
        with self._store.write("record_annotated_sources") as conn:
            conn.execute(insert(forge_annotation_facts), rows)


def _conforms_forge_annotation_store(x: ForgeAnnotationStore) -> IAnnotatedSources:
    return x
