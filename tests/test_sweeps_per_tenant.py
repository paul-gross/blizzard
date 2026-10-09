"""The hub's sweeps across tenants: each is one pass per hub that reads its corpus once and acts on every
row in that row's own tenant, through that tenant's half of the sweep — never one tenant's scope on another
tenant's row, and never on a tenant closed since the pass read its corpus."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import Chunk, PendingCloseIntent, WorkRef
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.domain.chunk.ports.work_refs import TenantWorkRef, WorkRefsSignature
from blizzard.hub.domain.observability.analytics.derivation import (
    DerivationTally,
    EventDerivationReconciler,
    TenantEventDerivation,
)
from blizzard.hub.domain.observability.analytics.events import CandidacyRead, DerivationSignature
from blizzard.hub.domain.observability.forge_status import AnnotationReconciler, AnnotationTally, TenantAnnotation
from blizzard.hub.domain.work_items.closure import (
    CloseDrainSweep,
    CloseDrainTally,
    CloseIntentDrainer,
    TenantCloseIntent,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import StoreScope, TenantClosed, TenantId
from tests.support import (
    HubHarness,
    build_hub,
    close_tenant,
    default_tenant,
    hub_level_connections,
    hub_scoped,
    make_graph,
    seed_chunk_record,
)

_NOW = datetime(2026, 7, 13, tzinfo=UTC)
_A = TenantId("ten_a")
_B = TenantId("ten_b")


def _refused(tenant: TenantId) -> object:
    raise TenantClosed(tenant)


# --- the close drain -----------------------------------------------------------------------


class _Intents:
    def __init__(self, rows: Sequence[TenantCloseIntent]) -> None:
        self.rows = list(rows)
        self.reads = 0

    def pending_close_intents(self) -> list[TenantCloseIntent]:
        self.reads += 1
        return self.rows


class _TenantDrainer:
    """Stands in for one tenant's :class:`CloseIntentDrainer`, recording the intents it is handed."""

    def __init__(self) -> None:
        self.drained: list[str] = []

    def drain(self, due: Sequence[PendingCloseIntent], tally: CloseDrainTally) -> None:
        self.drained.extend(intent.chunk_id for intent in due)


def _intent(tenant: TenantId, chunk_id: str) -> TenantCloseIntent:
    return TenantCloseIntent(tenant=tenant, intent=PendingCloseIntent(chunk_id=chunk_id, ref=WorkRef("src", "1")))


@pytest.mark.unit
def test_one_drain_pass_hands_each_tenant_only_its_own_intents_in_intent_order() -> None:
    corpus = _Intents([_intent(_A, "ch_a1"), _intent(_B, "ch_b1"), _intent(_A, "ch_a2")])
    drainers = {_A: _TenantDrainer(), _B: _TenantDrainer()}
    sweep = CloseDrainSweep(
        corpus=corpus, tenant=lambda tenant: cast(CloseIntentDrainer, drainers[tenant]), clock=FixedClock(_NOW)
    )

    sweep.sweep()

    assert corpus.reads == 1
    assert drainers[_A].drained == ["ch_a1", "ch_a2"]
    assert drainers[_B].drained == ["ch_b1"]


@pytest.mark.unit
def test_the_drain_skips_a_tenant_closed_since_its_corpus_read_and_drains_the_rest() -> None:
    drainer = _TenantDrainer()
    sweep = CloseDrainSweep(
        corpus=_Intents([_intent(_B, "ch_b1"), _intent(_A, "ch_a1")]),
        tenant=lambda tenant: cast(CloseIntentDrainer, drainer if tenant == _A else _refused(tenant)),
        clock=FixedClock(_NOW),
    )

    sweep.sweep()

    assert drainer.drained == ["ch_a1"]


# --- event derivation ----------------------------------------------------------------------


class _Segments:
    def __init__(self, candidacy: dict[TenantId, CandidacyRead], derived: dict[TenantId, frozenset[str]]) -> None:
        self._candidacy = candidacy
        self._derived = derived

    def derivation_signature(self) -> DerivationSignature:
        return DerivationSignature(segment_count=1, max_segment_id=1, max_received_at=_NOW, chunk_count=1)

    def candidacy(self, extractor_version: str) -> dict[TenantId, CandidacyRead]:
        return self._candidacy

    def derived_segment_ids(self) -> dict[TenantId, frozenset[str]]:
        return self._derived


class _TenantDerivation:
    """Stands in for one tenant's :class:`TenantEventDerivation`, recording what it is handed."""

    def __init__(self) -> None:
        self.handed: list[tuple[list[str], frozenset[str]]] = []

    def converge(self, candidates: Sequence[str], stale: frozenset[str], tally: DerivationTally) -> bool:
        self.handed.append((list(candidates), stale))
        return True


@pytest.mark.unit
def test_a_derivation_pass_keys_each_segment_by_its_tenant() -> None:
    """Both tenants hold a segment ``sg_1`` (segment ids are runner-minted): the one visible in
    ``ten_a`` is derived there, and only ``ten_b``'s, visible no longer, is dropped."""
    visible = CandidacyRead(visible_segment_ids=frozenset({"sg_1"}), candidate_segment_ids=["sg_1"])
    corpus = _Segments({_A: visible}, {_A: frozenset({"sg_1"}), _B: frozenset({"sg_1"})})
    units = {_A: _TenantDerivation(), _B: _TenantDerivation()}
    reconciler = EventDerivationReconciler(
        corpus=corpus, tenant=lambda tenant: cast(TenantEventDerivation, units[tenant]), clock=FixedClock(_NOW)
    )

    reconciler.sweep()

    assert units[_A].handed == [(["sg_1"], frozenset())]
    assert units[_B].handed == [([], frozenset({"sg_1"}))]


@pytest.mark.unit
def test_a_derivation_pass_skips_a_tenant_closed_since_its_corpus_read() -> None:
    read = CandidacyRead(visible_segment_ids=frozenset({"sg_1"}), candidate_segment_ids=["sg_1"])
    unit = _TenantDerivation()
    reconciler = EventDerivationReconciler(
        corpus=_Segments({_A: read, _B: read}, {}),
        tenant=lambda tenant: cast(TenantEventDerivation, unit if tenant == _A else _refused(tenant)),
        clock=FixedClock(_NOW),
    )

    reconciler.sweep()

    assert unit.handed == [(["sg_1"], frozenset())]


# --- forge-status annotation ---------------------------------------------------------------


class _WorkRefs:
    def __init__(self, rows: Sequence[TenantWorkRef]) -> None:
        self.rows = list(rows)
        self.reads = 0

    def live_work_refs(self) -> list[TenantWorkRef]:
        self.reads += 1
        return self.rows

    def live_work_refs_signature(self) -> WorkRefsSignature:
        return WorkRefsSignature(marks=())


class _TenantAnnotation:
    """Stands in for one tenant's :class:`TenantAnnotation`: its sources, and the rows it reconciled."""

    def __init__(self, *sources: str) -> None:
        self.sources = list(sources)
        self.reconciled: list[tuple[TenantId, list[str], list[str]]] = []

    def annotating(self) -> list[str]:
        return self.sources

    def reconcile(
        self,
        tenant: TenantId,
        annotating: Sequence[str],
        rows: Callable[[], Sequence[TenantWorkRef]],
        tally: AnnotationTally,
    ) -> bool:
        self.reconciled.append((tenant, list(annotating), [row.chunk_id for row in rows()]))
        return True


def _work_ref(tenant: TenantId, chunk_id: str) -> TenantWorkRef:
    return TenantWorkRef(tenant=tenant, chunk_id=chunk_id, ref=WorkRef("src", chunk_id))


@pytest.mark.unit
def test_an_annotation_pass_reconciles_each_tenants_rows_against_its_own_sources() -> None:
    corpus = _WorkRefs([_work_ref(_A, "ch_a"), _work_ref(_B, "ch_b")])
    units = {_A: _TenantAnnotation("forge-a"), _B: _TenantAnnotation("forge-b")}
    reconciler = AnnotationReconciler(
        corpus=corpus,
        tenants=lambda: [_B, _A],
        tenant=lambda tenant: cast(TenantAnnotation, units[tenant]),
        clock=FixedClock(_NOW),
        full_pass_floor=timedelta(minutes=10),
    )

    reconciler.sweep()

    assert corpus.reads == 1
    assert units[_A].reconciled == [(_A, ["forge-a"], ["ch_a"])]
    assert units[_B].reconciled == [(_B, ["forge-b"], ["ch_b"])]


@pytest.mark.unit
def test_an_annotation_pass_skips_a_tenant_closed_since_the_open_set_was_read() -> None:
    unit = _TenantAnnotation("forge-a")
    reconciler = AnnotationReconciler(
        corpus=_WorkRefs([_work_ref(_A, "ch_a"), _work_ref(_B, "ch_b")]),
        tenants=lambda: [_A, _B],
        tenant=lambda tenant: cast(TenantAnnotation, unit if tenant == _A else _refused(tenant)),
        clock=FixedClock(_NOW),
        full_pass_floor=timedelta(minutes=10),
    )

    reconciler.sweep()

    assert unit.reconciled == [(_A, ["forge-a"], ["ch_a"])]


# --- the close drain over two tenants of one hub --------------------------------------------


def _stores_of(hub: HubHarness, tenant: TenantId) -> ChunkStores:
    connections = hub_level_connections(hub.engine).scoped(StoreScope(tenant))
    return build_chunk_stores(connections, hub.clock, registry=RunnerRegistryStore(connections))


def _landed_hub_ref(hub: HubHarness, tenant: TenantId, chunk_id: str) -> ChunkStores:
    """A chunk of ``tenant`` holding the hub source's ref ``1`` — the same ref in every tenant — landed,
    so its close intent is pending; the tenant's chunk stores."""
    connections = hub_level_connections(hub.engine).scoped(StoreScope(tenant))
    graph_id = f"gr_{chunk_id}"
    GraphStore(connections).mint(make_graph(graph_id, "g"), definition_yaml="", at=hub.clock.now())
    stores = _stores_of(hub, tenant)
    seed_chunk_record(
        stores, Chunk(chunk_id=chunk_id, graph_id=graph_id, work_refs=[WorkRef("hub", "1")], minted_at=_NOW)
    )
    stores.artifacts.record_hub_artifact(
        chunk_id,
        node_id="nd_deliver",
        node_name="deliver",
        epoch=1,
        name="merged/widget",
        content="sha",
        at=hub.clock.now(),
        admission=EpochAdmission.AT_OR_ABOVE,
    )
    return stores


def _close_events(hub: HubHarness, tenant: TenantId) -> list[str]:
    connections = hub_level_connections(hub.engine).scoped(StoreScope(tenant))
    log = s.event_log
    with connections.read("test close events") as conn:
        rows = conn.execute(connections.select(log).where(log.c.kind.like("work-item-close%"))).all()
    return [row.chunk_id for row in rows]


@pytest.mark.component
def test_one_drain_pass_closes_each_tenants_intent_in_its_own_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    carried = default_tenant(hub.engine)
    other = hub.add_tenant("other")
    stores = {
        carried: _landed_hub_ref(hub, carried, "ch_carried"),
        other.tenant: _landed_hub_ref(hub, other.tenant, "ch_other"),
    }

    hub.hub_services.close_drain.sweep()

    assert stores[carried].delivery.pending_close_intents() == []
    assert stores[other.tenant].delivery.pending_close_intents() == []
    assert _close_events(hub, carried) == ["ch_carried"]
    assert _close_events(hub, other.tenant) == ["ch_other"]


@pytest.mark.component
def test_a_drain_pass_leaves_a_closed_tenants_intent_untouched(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    carried = default_tenant(hub.engine)
    other = hub.add_tenant("other")
    carried_stores = _landed_hub_ref(hub, carried, "ch_carried")
    other_stores = _landed_hub_ref(hub, other.tenant, "ch_other")
    close_tenant(hub.engine, other.tenant)

    hub.hub_services.close_drain.sweep()

    assert carried_stores.delivery.pending_close_intents() == []
    assert [intent.chunk_id for intent in other_stores.delivery.pending_close_intents()] == ["ch_other"]
    assert _close_events(hub, other.tenant) == []
    attempts = s.close_intent_attempts
    with hub.engine.connect() as conn:
        tried = conn.execute(
            hub_scoped(sa.select(sa.func.count()).select_from(attempts).where(attempts.c.tenant_id == other.tenant))
        ).scalar_one()
    assert tried == 0
