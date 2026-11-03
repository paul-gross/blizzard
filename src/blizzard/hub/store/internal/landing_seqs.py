"""A project's landing-order sequence, read and checked on the caller's connection — shared by the position store and
the declarative apply, the two writers of ``project_repository_positions`` (package-private).

The sequence is the id of the project's newest position fact, ``0`` when it holds none. Every change to a landing
order appends a fact, so a write planned on one sequence lands only while no fact for its project is newer
(``bzh:store-refuses-stale``). The check and the append share the caller's write transaction, which the tenant-row
lock serializes against every other writer of the tenant."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func
from sqlalchemy.engine import Connection

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.domain.projects.order import LandingOrderConflict
from blizzard.hub.store.schema import project_repository_positions
from blizzard.hub.store.scoped import statements_of


def landing_seqs(conn: Connection, project_ids: Sequence[ProjectId]) -> dict[ProjectId, int]:
    """The landing-order sequence of each of ``project_ids``: one group-by-max per batch."""
    stmts = statements_of(conn)
    facts = project_repository_positions
    found = dict.fromkeys(project_ids, 0)
    for batch in id_batches(sorted(set(project_ids))):
        rows = conn.execute(
            stmts.select(facts, facts.c.project_id, func.max(facts.c.id))
            .where(facts.c.project_id.in_(batch))
            .group_by(facts.c.project_id)
        ).all()
        found.update((ProjectId(row[0]), row[1]) for row in rows)
    return found


def require_landing_seq(conn: Connection, project_id: ProjectId, from_seq: int) -> None:
    """:class:`LandingOrderConflict` when the project's sequence no longer stands at ``from_seq``."""
    if landing_seqs(conn, [project_id])[project_id] != from_seq:
        raise LandingOrderConflict(project_id, expected=from_seq)
