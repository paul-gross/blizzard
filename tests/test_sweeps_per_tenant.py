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
from blizzard.hub.domain.tenancy.teardown import IReadTenantTeardowns, TenantTeardown, TenantTeardownSweep
from blizzard.hub.domain.work_items.closure import (
    CLOSE_DRAIN_PASS_LIMIT,
    CloseDrainSweep,
    CloseDrainTally,
    CloseIntentDrainer,
    TenantCloseIntent,
    fair_share,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import StoreScope, TenantClosed, TenantId
from tests.pass_isolation import HEALTHY, Poison, Recorder, Scene, assert_pass_isolates
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

    def derivation_signature(self) -> dict[TenantId, DerivationSignature]:
        signature = DerivationSignature(segment_count=1, max_segment_id=1, max_received_at=_NOW, chunk_count=1)
        return dict.fromkeys({*self._candidacy, *self._derived}, signature)

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

    def live_work_refs_signature(self) -> dict[TenantId, WorkRefsSignature]:
        return {row.tenant: WorkRefsSignature(marks=()) for row in self.rows}


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


# --- one tenant's failure or burst changes nothing the pass does for another (bzh:pass-isolates-tenants) ----------


def _poison_for(tenant: TenantId, scene: Scene) -> Poison | None:
    return scene.poison if tenant == scene.poisoned else None


def _annotation_pass(scene: Scene, recorder: Recorder, clock: FixedClock) -> Callable[[], None]:
    class Unit(_TenantAnnotation):
        def __init__(self, tenant: TenantId) -> None:
            super().__init__("forge")
            self.tenant = tenant

        def annotating(self) -> list[str]:
            recorder.log(self.tenant, "annotating")
            return super().annotating()

        def reconcile(
            self,
            tenant: TenantId,
            annotating: Sequence[str],
            rows: Callable[[], Sequence[TenantWorkRef]],
            tally: AnnotationTally,
        ) -> bool:
            recorder.log(tenant, "reconcile", list(annotating), [row.chunk_id for row in rows()])
            poison = _poison_for(tenant, scene)
            if poison is Poison.RAISES:
                raise RuntimeError("the forge cannot be read")
            return poison is None

    corpus = _WorkRefs([_work_ref(tenant, f"ch_{tenant}") for tenant in scene.tenants])
    reconciler = AnnotationReconciler(
        corpus=corpus,
        tenants=lambda: scene.tenants,
        tenant=lambda tenant: cast(TenantAnnotation, Unit(tenant)),
        clock=clock,
        full_pass_floor=timedelta(minutes=10),
    )
    return reconciler.sweep


def _derivation_pass(scene: Scene, recorder: Recorder, clock: FixedClock) -> Callable[[], None]:
    class Unit(_TenantDerivation):
        def __init__(self, tenant: TenantId) -> None:
            super().__init__()
            self.tenant = tenant

        def converge(self, candidates: Sequence[str], stale: frozenset[str], tally: DerivationTally) -> bool:
            recorder.log(self.tenant, "converge", list(candidates), sorted(stale))
            poison = _poison_for(self.tenant, scene)
            if poison is Poison.RAISES:
                raise RuntimeError("the pins cannot be resolved")
            return poison is None

    reads = {
        tenant: CandidacyRead(visible_segment_ids=frozenset({f"sg_{tenant}"}), candidate_segment_ids=[f"sg_{tenant}"])
        for tenant in scene.tenants
    }
    reconciler = EventDerivationReconciler(
        corpus=_Segments(reads, {}), tenant=lambda tenant: cast(TenantEventDerivation, Unit(tenant)), clock=clock
    )
    return reconciler.sweep


def _drain_pass(scene: Scene, recorder: Recorder, clock: FixedClock) -> Callable[[], None]:
    pending = [
        _intent(tenant, f"ch_{tenant}_{n}")
        for tenant in (*([scene.poisoned] if scene.poisoned else []), *HEALTHY)
        for n in range(CLOSE_DRAIN_PASS_LIMIT + 20 if tenant == scene.poisoned and scene.poison is Poison.BURST else 3)
    ]
    corpus = _Intents(pending)

    class Drainer(_TenantDrainer):
        def __init__(self, tenant: TenantId) -> None:
            super().__init__()
            self.tenant = tenant

        def drain(self, due: Sequence[PendingCloseIntent], tally: CloseDrainTally) -> None:
            recorder.log(self.tenant, "drain", [intent.chunk_id for intent in due])
            if _poison_for(self.tenant, scene) is Poison.RAISES:
                raise RuntimeError("the forge refused the close")
            drained = {intent.chunk_id for intent in due}
            corpus.rows = [row for row in corpus.rows if row.intent.chunk_id not in drained]

    return CloseDrainSweep(
        corpus=corpus, tenant=lambda tenant: cast(CloseIntentDrainer, Drainer(tenant)), clock=clock
    ).sweep


def _teardown_pass(scene: Scene, recorder: Recorder, clock: FixedClock) -> Callable[[], None]:
    class Teardown:
        def advance(self, tenant: TenantId, *, budget: int) -> None:
            recorder.log(tenant, "advance", budget)
            if _poison_for(tenant, scene) is Poison.RAISES:
                raise RuntimeError("the drain failed")

    class Underway:
        def underway(self) -> list[TenantId]:
            return list(scene.tenants)

    return TenantTeardownSweep(
        teardown=cast(TenantTeardown, Teardown()), teardowns=cast(IReadTenantTeardowns, Underway()), budget=50
    ).sweep


_FAILING = (Poison.RAISES, Poison.UNCONVERGED)


@pytest.mark.unit
@pytest.mark.parametrize("poison", _FAILING)
def test_a_failing_tenant_changes_nothing_the_annotation_pass_does_for_another(poison: Poison) -> None:
    assert_pass_isolates(_annotation_pass, poison)


@pytest.mark.unit
@pytest.mark.parametrize("poison", _FAILING)
def test_a_failing_tenant_changes_nothing_the_derivation_pass_does_for_another(poison: Poison) -> None:
    assert_pass_isolates(_derivation_pass, poison)


@pytest.mark.unit
@pytest.mark.parametrize("poison", (Poison.RAISES, Poison.BURST))
def test_a_failing_or_bursting_tenant_changes_nothing_the_close_drain_does_for_another(poison: Poison) -> None:
    assert_pass_isolates(_drain_pass, poison)


@pytest.mark.unit
@pytest.mark.parametrize("poison", (Poison.RAISES, Poison.BURST))
def test_a_failing_or_bursting_tenant_changes_nothing_the_teardown_sweep_does_for_another(poison: Poison) -> None:
    assert_pass_isolates(_teardown_pass, poison)


def _due(tenant: TenantId, count: int) -> list[PendingCloseIntent]:
    return [PendingCloseIntent(chunk_id=f"ch_{tenant}_{n}", ref=WorkRef("src", str(n))) for n in range(count)]


@pytest.mark.unit
def test_the_fair_share_takes_one_intent_per_tenant_in_turn_and_hands_unused_share_on() -> None:
    due = {_A: _due(_A, 5), _B: _due(_B, 1)}

    taken = fair_share(due, 4, start=0)

    assert {tenant: [i.chunk_id for i in intents] for tenant, intents in taken.items()} == {
        _A: ["ch_ten_a_0", "ch_ten_a_1", "ch_ten_a_2"],
        _B: ["ch_ten_b_0"],
    }


@pytest.mark.unit
def test_the_fair_share_rotates_which_tenant_takes_the_odd_slot() -> None:
    due = {_A: _due(_A, 3), _B: _due(_B, 3)}

    first = fair_share(due, 3, start=0)
    second = fair_share(due, 3, start=1)

    assert (len(first[_A]), len(first[_B])) == (2, 1)
    assert (len(second[_A]), len(second[_B])) == (1, 2)


@pytest.mark.unit
def test_the_fair_share_never_exceeds_the_limit_and_takes_everything_below_it() -> None:
    due = {_A: _due(_A, 4), _B: _due(_B, 4)}

    assert sum(map(len, fair_share(due, 5, start=0).values())) == 5
    assert sum(map(len, fair_share(due, 50, start=0).values())) == 8
    assert fair_share({}, 5, start=3) == {}


class _VanishedTenant(_Segments):
    """``_B`` holds derived rows but no segment or chunk, so the grouped probe has no entry for it."""

    def derivation_signature(self) -> dict[TenantId, DerivationSignature]:
        return {_A: super().derivation_signature()[_A]}


@pytest.mark.unit
def test_a_derivation_pass_still_drops_a_tenant_whose_every_segment_and_chunk_is_gone() -> None:
    read = CandidacyRead(visible_segment_ids=frozenset({"sg_1"}), candidate_segment_ids=["sg_1"])
    units = {_A: _TenantDerivation(), _B: _TenantDerivation()}
    reconciler = EventDerivationReconciler(
        corpus=_VanishedTenant({_A: read}, {_A: frozenset({"sg_1"}), _B: frozenset({"sg_9"})}),
        tenant=lambda tenant: cast(TenantEventDerivation, units[tenant]),
        clock=FixedClock(_NOW),
    )

    reconciler.sweep()

    assert units[_B].handed == [([], frozenset({"sg_9"}))]


@pytest.mark.unit
def test_a_derivation_pass_retries_a_vanished_tenants_drop_that_raised() -> None:
    read = CandidacyRead(visible_segment_ids=frozenset({"sg_1"}), candidate_segment_ids=["sg_1"])
    attempts: list[frozenset[str]] = []

    class Flaky(_TenantDerivation):
        def converge(self, candidates: Sequence[str], stale: frozenset[str], tally: DerivationTally) -> bool:
            attempts.append(stale)
            if len(attempts) == 1:
                raise RuntimeError("the drop failed")
            return True

    units = {_A: _TenantDerivation(), _B: Flaky()}
    reconciler = EventDerivationReconciler(
        corpus=_VanishedTenant({_A: read}, {_A: frozenset({"sg_1"}), _B: frozenset({"sg_9"})}),
        tenant=lambda tenant: cast(TenantEventDerivation, units[tenant]),
        clock=FixedClock(_NOW),
    )

    reconciler.sweep()
    reconciler.sweep()
    reconciler.sweep()

    assert attempts == [frozenset({"sg_9"}), frozenset({"sg_9"})]
