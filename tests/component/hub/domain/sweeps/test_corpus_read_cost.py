"""What a gated sweep's due pass reads over a real, migrated store holding two tenants: with one tenant due
and the other's corpus growing between two measurements, the rows read and the statements issued stay put
(``bzh:probe-gated-pass``, ``bzh:page-bounded-read``). The tenants' halves are stand-ins, so what is counted
is the pass's own probe and corpus reads."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.chunk.model import PendingCloseIntent
from blizzard.hub.domain.chunk.ports.work_refs import TenantWorkRef
from blizzard.hub.domain.observability.analytics.derivation import (
    DerivationTally,
    EventDerivationReconciler,
    TenantEventDerivation,
)
from blizzard.hub.domain.observability.forge_status import AnnotationReconciler, AnnotationTally, TenantAnnotation
from blizzard.hub.domain.work_items.closure import CloseDrainSweep, CloseDrainTally, CloseIntentDrainer
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.hub_scoped_reads import build_hub_scoped_reads
from blizzard.hub.tenancy import TenantId
from tests.support.scaffolding import (
    add_tenant,
    count_queries,
    count_rows_read,
    default_project,
    default_tenant,
    hub_level_connections,
    hub_scoped,
    migrated_hub_at,
)

_AT = datetime(2026, 10, 8, tzinfo=UTC)
#: Half a convergence floor, so two of them make a tenant due and one does not.
_HALF_FLOOR = timedelta(minutes=5)
_EXTRACTOR = "x1"


class _World:
    """The default tenant, whose corpus never changes, and another whose corpus :meth:`grow` adds to; each starts
    holding one live chunk with a work ref, a pending close intent and a visible, derived transcript segment."""

    def __init__(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path / 'hub.db'}"
        migrated_hub_at(db_url)
        self.engine = create_engine_from_url(db_url)
        self.measured = default_tenant(self.engine)
        self.other = add_tenant(self.engine, "other")
        self.clock = FixedClock(_AT)
        self.corpus = build_hub_scoped_reads(hub_level_connections(self.engine)).corpus
        self._seq = itertools.count()
        for tenant in (self.measured, self.other):
            self._insert(
                s.graphs,
                tenant_id=tenant,
                graph_id=f"gr_{tenant}",
                name="g",
                entry_node_id="nd_1",
                definition_yaml="",
                created_at=_AT,
            )
            self.grow(tenant, 1)

    def tenants(self) -> list[TenantId]:
        return [self.measured, self.other]

    def _insert(self, table: sa.Table, **values: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(hub_scoped(sa.insert(table).values(**values)))

    def grow(self, tenant: TenantId, count: int) -> None:
        """``count`` more chunks of ``tenant``, each adding a row to every corpus the sweeps read."""
        for _ in range(count):
            chunk_id = f"ch_{next(self._seq)}"
            self._insert(
                s.chunks,
                tenant_id=tenant,
                project_id=default_project(self.engine, tenant),
                chunk_id=chunk_id,
                graph_id=f"gr_{tenant}",
                minted_at=_AT,
                model="",
            )
            self._insert(s.chunk_work_refs, tenant_id=tenant, chunk_id=chunk_id, source="src", ref=chunk_id)
            self._insert(
                s.close_intents, tenant_id=tenant, chunk_id=chunk_id, source="src", ref=chunk_id, enqueued_at=_AT
            )
            self._insert(
                s.transcript_segments,
                tenant_id=tenant,
                segment_id=f"sg_{chunk_id}",
                chunk_id=chunk_id,
                node_id="nd_1",
                epoch=1,
                spawn_generation=0,
                runner_id="r1",
                turn_range_start=0,
                turn_range_end=1,
                final=True,
                rejected=False,
                byte_count=0,
                normalizer_version="n1",
                received_at=_AT,
                content_digest="d",
            )
            self._insert(
                s.transcript_event_derivations,
                tenant_id=tenant,
                segment_id=f"sg_{chunk_id}",
                extractor_version=_EXTRACTOR,
                content_fingerprint="stale",
                derived_at=_AT,
                event_count=0,
                complete=True,
            )


class _Derivation:
    def converge(self, candidates: Sequence[str], stale: frozenset[str], tally: DerivationTally) -> bool:
        return True


class _Annotation:
    def annotating(self) -> list[str]:
        return ["src"]

    def reconcile(
        self,
        tenant: TenantId,
        annotating: Sequence[str],
        rows: Callable[[], Sequence[TenantWorkRef]],
        tally: AnnotationTally,
        outages: set[str],
    ) -> bool:
        assert rows()  # the corpus is read only when its rows are asked for
        return True


class _Drainer:
    def drain(self, due: Sequence[PendingCloseIntent], tally: CloseDrainTally) -> None:
        pass


def _derivation(world: _World) -> EventDerivationReconciler:
    half = cast(TenantEventDerivation, _Derivation())
    return EventDerivationReconciler(
        corpus=world.corpus, tenant=lambda _tenant: half, clock=world.clock, extractor_version=_EXTRACTOR
    )


def _annotation(world: _World) -> AnnotationReconciler:
    half = cast(TenantAnnotation, _Annotation())
    return AnnotationReconciler(
        corpus=world.corpus,
        tenants=world.tenants,
        tenant=lambda _tenant: half,
        clock=world.clock,
        full_pass_floor=2 * _HALF_FLOOR,
    )


_Gated = EventDerivationReconciler | AnnotationReconciler


def _cost_of_due_passes(world: _World, sweep: _Gated, measured_pass: Callable[[], object]) -> list[int]:
    """``measured_pass``'s cost with the other tenant's corpus at two sizes, only the measured tenant due: its floor
    has elapsed, and the other, converged again after growing, is inside its own. Statements then rows, each from a
    pass of its own."""
    costs: list[int] = []
    sweep.sweep()  # every tenant converged
    for grown in (1, 20):
        for count in (count_queries, count_rows_read):
            world.clock.advance(_HALF_FLOOR)
            world.grow(world.other, grown)
            assert sweep.sweep_tenant(world.other) is None
            world.clock.advance(_HALF_FLOOR)
            costs.append(count(world.engine, measured_pass))
    return costs


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


@pytest.mark.parametrize(
    ("build", "statements"), [(_derivation, 4), (_annotation, 2)], ids=["derivation", "annotation"]
)
def test_a_due_hub_wide_pass_reads_no_more_as_a_tenant_not_due_grows(
    world: _World, build: Callable[[_World], _Gated], statements: int
) -> None:
    sweep = build(world)

    small_statements, small_rows, large_statements, large_rows = _cost_of_due_passes(world, sweep, sweep.sweep)

    assert (small_statements, large_statements) == (statements, statements)
    assert small_rows == large_rows


@pytest.mark.parametrize(
    ("build", "statements"), [(_derivation, 4), (_annotation, 2)], ids=["derivation", "annotation"]
)
def test_a_due_one_tenant_pass_reads_no_more_as_another_tenant_grows(
    world: _World, build: Callable[[_World], _Gated], statements: int
) -> None:
    sweep = build(world)

    small_statements, small_rows, large_statements, large_rows = _cost_of_due_passes(
        world, sweep, lambda: sweep.sweep_tenant(world.measured)
    )

    assert (small_statements, large_statements) == (statements, statements)
    assert small_rows == large_rows


def test_a_one_tenant_drain_pass_reads_no_more_as_another_tenants_backlog_grows(world: _World) -> None:
    drainer = cast(CloseIntentDrainer, _Drainer())
    sweep = CloseDrainSweep(corpus=world.corpus, tenant=lambda _tenant: drainer, clock=world.clock)

    def measured_pass() -> object:
        return sweep.sweep_tenant(world.measured)

    costs: list[tuple[int, int]] = []
    for grown in (1, 20):
        world.grow(world.other, grown)
        costs.append((count_queries(world.engine, measured_pass), count_rows_read(world.engine, measured_pass)))

    assert [statements for statements, _ in costs] == [1, 1]
    assert costs[0][1] == costs[1][1]
