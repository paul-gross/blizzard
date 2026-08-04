"""The worker-facing chunk-history projection (issue #237).

``HistoryRowView`` is a flat, kind-discriminated row — ``transition`` | ``migration`` |
``bounce`` — merged oldest-first across all three of a chunk's own hub-side histories
(``ChunkDetail.history``/``.migrations``/``.bounces``). Built fresh here rather than
mounting the board's own views as the runner's ``response_model``: a bounce carries no
epoch to join it onto a transition row, and a fresh flat row keeps ``wire.chunk``'s views
out of the runner's OpenAPI spec (pinned by
tests/test_pin_wire.py::test_the_runner_spec_carries_no_chunk_detail_history_views).

``ChunkHistoryView`` is the internal projection this route validates the hub's full
``ChunkDetail`` payload down to before ``history_rows`` runs — never a FastAPI
``response_model``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from blizzard.wire.chunk import BounceView, MigrationView, TransitionView


class ChunkHistoryView(BaseModel):
    """The slice of a hub ``ChunkDetail`` payload ``history_rows`` needs — validated with
    pydantic's default ``extra="ignore"``, so it decodes straight off the full aggregate
    without duplicating every other field. The three fields are **required**, not
    defaulted to ``[]`` (issue #237), so a hub-side rename fails loudly here instead of
    decoding as "no history yet" (pinned by
    tests/test_pin_wire.py::test_chunk_history_view_requires_all_three_history_lists)."""

    history: list[TransitionView]
    migrations: list[MigrationView]
    bounces: list[BounceView]


class HistoryRowView(BaseModel):
    """One row of a chunk's own timeline, as a worker reads it — a transition, a
    cross-graph migration, or a delivery bounce, merged oldest-first by ``recorded_at``.

    ``from_node``/``to_node`` are human-legible labels: a transition's node names, or (for
    a migration) the ``graph/node`` hop
    (``from_graph/from_node --choice--> to_graph/landed_node``, see ``MigrationView``).
    Both null for a bounce, which names no node. ``epoch`` is populated only for a
    transition row — the wire's own ``MigrationView``/``BounceView`` carry no epoch to
    project. ``cause``/``detail`` carry a bounce's kick-back cause and its raw envelope, or
    a migration's ``source`` (``authored-edge``/``intent``/``follow-latest``) in
    ``detail``; both null on a transition row.
    """

    kind: Literal["transition", "migration", "bounce"]
    from_node: str | None = None
    to_node: str | None = None
    choice: str | None = None
    epoch: int | None = None
    graph_name: str | None = None
    cause: str | None = None
    detail: str | None = None
    recorded_at: str


def _transition_row(t: TransitionView) -> HistoryRowView:
    return HistoryRowView(
        kind="transition",
        from_node=t.from_node_name or t.from_node_id,
        to_node=t.to_node_name or t.to_node_id,
        choice=t.choice_name,
        epoch=t.epoch,
        graph_name=t.graph_name,
        recorded_at=t.recorded_at,
    )


def _migration_row(m: MigrationView) -> HistoryRowView:
    from_label = "/".join(p for p in (m.from_graph_name, m.from_node_name or m.from_node_id) if p)
    to_label = "/".join(p for p in (m.to_graph_name, m.landed_node_name or m.landed_node_id) if p)
    return HistoryRowView(
        kind="migration",
        from_node=from_label or None,
        to_node=to_label or None,
        choice=m.choice_name,
        graph_name=m.to_graph_name,
        detail=m.source,
        recorded_at=m.recorded_at,
    )


def _bounce_row(b: BounceView) -> HistoryRowView:
    return HistoryRowView(kind="bounce", cause=b.cause, detail=b.envelope, recorded_at=b.recorded_at)


def history_rows(detail: ChunkHistoryView) -> list[HistoryRowView]:
    """The chunk's transitions, migrations, and bounces merged into one kind-discriminated
    timeline, oldest-first by ``recorded_at``. Each
    input list already arrives oldest-first, so a stable sort on ``recorded_at`` alone
    preserves each kind's own order and only interleaves across kinds."""
    rows = (
        [_transition_row(t) for t in detail.history]
        + [_migration_row(m) for m in detail.migrations]
        + [_bounce_row(b) for b in detail.bounces]
    )
    return sorted(rows, key=lambda r: r.recorded_at)
