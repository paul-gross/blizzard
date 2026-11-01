"""The trace export's store reads (component tier) — facts written through the hub's real routes,
hydrated to ``StepFacts`` and selected through the window the sweep and replay share."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.hub.domain.observability.egress.rows import step_row
from blizzard.hub.domain.observability.hub_events import HubEventKind
from blizzard.hub.domain.observability.tracing.assembly import assemble_step
from blizzard.hub.domain.observability.tracing.attributes import RUNNER_ID, RUNNER_NAME
from blizzard.hub.domain.observability.tracing.cursor import CursorKey
from blizzard.hub.domain.observability.tracing.facts import (
    StepFacts,
    TracedChunkStop,
    TracedEpochOwner,
    TracedLease,
    TracedRouteRelease,
    TracedTransition,
)
from blizzard.hub.domain.observability.tracing.repository import ClosingCandidates, StepHydration, TraceCheckpoint
from blizzard.hub.domain.observability.tracing.steps import StepOutcome, identify_steps
from blizzard.hub.domain.observability.tracing.summary import summarize_step
from blizzard.hub.domain.observability.tracing.window import oldest_unsent, read_window
from blizzard.hub.store.internal.trace_store import ProjectLabels, TraceStore
from blizzard.hub.tenancy import TenantId
from tests.hub_event_rows import hub_event_log_of
from tests.support import (
    SEEDED_CAPABILITIES,
    HubHarness,
    added_runner,
    count_queries,
    default_tenant,
    seed_runner,
)
from tests.trace_hub import seed_open_tenant_chunk, stop, trace_hub, trace_store, transitioned_and_stopped

pytestmark = pytest.mark.component


def test_hydrated_facts_identify_the_same_steps_as_a_hand_built_fixture(tmp_path: Path) -> None:
    hub, graph = trace_hub(tmp_path)
    moved, stopped = transitioned_and_stopped(hub, graph, 1)
    at = hub.clock.now()
    claimed = at - timedelta(seconds=5)
    build = next(n for n in graph.nodes if n.name == "build")
    review = next(n for n in graph.nodes if n.name == "review")

    hydrated = trace_store(hub).step_facts_for([moved, stopped, "ch_missing"])

    assert set(hydrated) == {moved, stopped}
    graphs = {graph.graph_id: graph}
    expected_moved = StepFacts(
        chunk_id=moved,
        graphs=graphs,
        pin_graph_id=graph.graph_id,
        lease_facts=(TracedLease(1, claimed),),
        epoch_owners=(TracedEpochOwner(1, "r1", claimed),),
        transitions=(
            TracedTransition(
                epoch=1,
                recorded_at=at,
                graph_id=graph.graph_id,
                to_node_id=review.node_id,
                from_node_id=build.node_id,
                choice_name="pass",
            ),
        ),
    )
    expected_stopped = StepFacts(
        chunk_id=stopped,
        graphs=graphs,
        pin_graph_id=graph.graph_id,
        lease_facts=(TracedLease(1, claimed),),
        epoch_owners=(TracedEpochOwner(1, "r1", claimed),),
        route_released=(TracedRouteRelease(at),),
        chunk_stopped=(TracedChunkStop(at),),
    )
    assert identify_steps(hydrated[moved]) == identify_steps(expected_moved)
    assert identify_steps(hydrated[stopped]) == identify_steps(expected_stopped)
    assert [s.close.outcome for s in identify_steps(hydrated[stopped]) if s.close] == [StepOutcome.RELEASED]
    assert hydrated[moved].work_refs == ("default#1",)
    assert hydrated[moved].work_sources == ("default",)
    assert set(hydrated[moved].graphs) == {graph.graph_id}


def test_a_rows_work_refs_and_sources_render_each_sources_slug_as_of_its_read(tmp_path: Path) -> None:
    """The stored ref keeps its source's key; each read renders that source's slug then, so a read after a slug
    change follows it and the summary and the egress ``sources`` column carry the slug, never the key."""
    hub, graph = trace_hub(tmp_path)
    moved, _ = transitioned_and_stopped(hub, graph, 1)
    slugs = {"default": "acme"}

    def hydration(tenant: TenantId) -> StepHydration:
        services = hub.open_services(tenant)
        return StepHydration(
            graphs=services.graphs,
            names=services.registry,
            projects=ProjectLabels(services.projects),
            label=lambda ref: f"{slugs[ref.source]}#{ref.ref}",
            source_label=lambda source: slugs[source],
        )

    store = trace_store(hub, hydration=hydration)

    def rendered() -> tuple[tuple[str, ...], tuple[str, ...]]:
        facts = store.step_facts_for([moved])[moved]
        steps = identify_steps(facts)
        row = step_row(summarize_step(facts, steps[0], steps), hub.clock.now())
        return facts.work_refs, row.sources

    before = rendered()
    slugs["default"] = "acme-core"
    after = rendered()

    assert before == (("acme#1",), ("acme",))
    assert after == (("acme-core#1",), ("acme-core",))


_RN = "rn_01JZ8Q4V6XK3M2N5P7R9T1W3Y5"


def test_a_step_names_its_runner_by_id_and_latest_registered_name_and_a_rename_by_restart_retells_it(
    tmp_path: Path,
) -> None:
    hub, graph = trace_hub(tmp_path)
    seed_runner(hub, _RN, name="r-claude")
    moved, _ = transitioned_and_stopped(hub, graph, 1, runner_id=_RN)
    store = trace_store(hub)

    def first_step_root() -> tuple[object, object]:
        facts = store.step_facts_for([moved])[moved]
        steps = identify_steps(facts)
        root = assemble_step(facts, steps[0], steps)[0]
        return root.attributes[RUNNER_ID], root.attributes[RUNNER_NAME]

    told = first_step_root()
    hub.services.fleet.register(added_runner(hub, _RN), "w1", name="r-claude-2", capabilities=SEEDED_CAPABILITIES)
    retold = first_step_root()

    assert told == (_RN, "r-claude")
    assert retold == (_RN, "r-claude-2")


def _drain(store: TraceStore, since: CursorKey, until, limit: int) -> list[CursorKey]:  #
    """Every key the window tells, pass after pass, until its position stops moving."""
    told: list[CursorKey] = []
    for _ in range(50):
        window = read_window(store, since, until, limit)
        told += [c.key for c in window.closed_steps()]
        if window.position == since:
            return told
        since = window.position
    raise AssertionError("the window never settled")


@pytest.mark.parametrize("limit", [1, 2, 50])
def test_ties_across_tables_and_ambient_closers_are_told_exactly_once(tmp_path: Path, limit: int) -> None:
    hub, graph = trace_hub(tmp_path)
    moved, stopped = transitioned_and_stopped(hub, graph, 1)
    tie = hub.clock.now()
    hub.clock.advance(timedelta(seconds=10))
    stop(hub, moved)

    told = _drain(trace_store(hub), CursorKey.opening(tie - timedelta(seconds=1)), tie + timedelta(hours=1), limit)

    # The later stop closes nothing new: the moved chunk's only step closed at the tie.
    assert told == sorted([CursorKey(tie, moved, 1), CursorKey(tie, stopped, 1)])


def test_a_window_reading_from_a_tie_tells_only_what_sorts_after_the_cursor(tmp_path: Path) -> None:
    hub, graph = trace_hub(tmp_path)
    moved, stopped = transitioned_and_stopped(hub, graph, 1)
    first, second = sorted([CursorKey(hub.clock.now(), moved, 1), CursorKey(hub.clock.now(), stopped, 1)])

    window = read_window(trace_store(hub), first, hub.clock.now() + timedelta(hours=1), 50)

    assert [c.key for c in window.closed_steps()] == [second]


def test_until_holds_back_steps_closing_after_it(tmp_path: Path) -> None:
    hub, graph = trace_hub(tmp_path)
    transitioned_and_stopped(hub, graph, 1)
    now = hub.clock.now()

    window = read_window(
        trace_store(hub), CursorKey.opening(now - timedelta(seconds=1)), now - timedelta(microseconds=1), 50
    )

    assert window.closed_steps() == ()


_OTHER = "ten_other"
_OTHER_CHUNK = "ch_other"


@pytest.mark.parametrize("limit", [1, 50])
def test_each_open_tenants_chunks_are_hydrated_in_their_own_tenant_and_told(tmp_path: Path, limit: int) -> None:
    hub, graph = trace_hub(tmp_path)
    moved, stopped = transitioned_and_stopped(hub, graph, 1)
    tie = hub.clock.now()
    seed_open_tenant_chunk(hub, _OTHER, _OTHER_CHUNK, tie)
    store = trace_store(hub)

    hydrated = store.step_facts_for([moved, _OTHER_CHUNK])
    since = CursorKey.opening(tie - timedelta(seconds=1))
    told = _drain(store, since, tie + timedelta(hours=1), limit)

    carried = default_tenant(hub.engine)
    assert {chunk_id: (f.tenant, f.tenant_name) for chunk_id, f in hydrated.items()} == {
        moved: (carried, hub.hub_services.tenants.open_tenants.name(carried)),
        _OTHER_CHUNK: (_OTHER, _OTHER),
    }
    assert set(hydrated[_OTHER_CHUNK].graphs) == {f"gr_{_OTHER}"}
    assert set(hydrated[moved].graphs) == {graph.graph_id}
    assert told == sorted(
        [CursorKey(tie, moved, 1), CursorKey(tie, stopped, 1), CursorKey(tie + timedelta(seconds=1), _OTHER_CHUNK, 1)]
    )


def _fleet(tmp_path: Path, pairs: int) -> HubHarness:
    tmp_path.mkdir()
    hub, graph = trace_hub(tmp_path)
    for i in range(pairs):
        transitioned_and_stopped(hub, graph, 10 * (i + 1))
    return hub


def test_statement_count_is_flat_as_the_window_grows(tmp_path: Path) -> None:
    small = _fleet(tmp_path / "small", 1)
    large = _fleet(tmp_path / "large", 4)
    counts: dict[str, int] = {}
    sizes: dict[str, int] = {}
    for name, hub in (("small", small), ("large", large)):
        store = trace_store(hub)
        since = CursorKey.opening(hub.clock.now() - timedelta(hours=1))

        def call(store: TraceStore = store, since: CursorKey = since, name: str = name) -> None:
            sizes[name] = len(read_window(store, since, since.at + timedelta(hours=2), 50).closed_steps())

        counts[name] = count_queries(hub.engine, call)
    assert sizes == {"small": 2, "large": 8}
    assert counts["small"] == counts["large"]


class _CountingReads:
    def __init__(self, store: TraceStore) -> None:
        self._store = store
        self.candidate_reads: list[int] = []

    def closing_candidates(self, since, until, limit):  #
        self.candidate_reads.append(limit)
        return self._store.closing_candidates(since, until, limit)

    def step_facts_for(self, chunk_ids):  #
        return self._store.step_facts_for(chunk_ids)


def test_oldest_unsent_is_the_first_key_the_drain_tells_and_reads_in_pages(tmp_path: Path) -> None:
    hub, graph = trace_hub(tmp_path)
    for i in range(3):
        transitioned_and_stopped(hub, graph, 10 * (i + 1))
    since = CursorKey.opening(hub.clock.now() - timedelta(hours=1))
    until = since.at + timedelta(hours=2)
    store = trace_store(hub)
    reads = _CountingReads(store)

    oldest = oldest_unsent(reads, since, until)  # type: ignore[arg-type]

    assert oldest is not None
    assert oldest.key == read_window(store, since, until, 1000).items[0].key
    assert reads.candidate_reads == [100]


class _NonClosingBacklog:
    """A backlog of ``depth`` candidates none of which closes anything, served ``limit`` at a time."""

    def __init__(self, depth: int) -> None:
        self._ids = [f"ch_{i:04d}" for i in range(depth)]
        self.limits: list[int] = []
        self.hydrated = 0

    def closing_candidates(self, since, until, limit):  #
        self.limits.append(limit)
        served = (len(self.limits) - 1) * limit
        page = tuple(self._ids[served : served + limit])
        if not page:
            return ClosingCandidates(())
        at = since + timedelta(seconds=1)
        if served + limit < len(self._ids):
            return ClosingCandidates(page, frontier=at)
        return ClosingCandidates(page, newest=at)

    def step_facts_for(self, chunk_ids):  #
        self.hydrated += 1
        return {}


@pytest.mark.parametrize(("depth", "pages"), [(100, 1), (150, 2), (350, 4)])
def test_oldest_unsent_passes_grow_by_pages_of_candidates_not_by_candidates(depth: int, pages: int) -> None:
    backlog = _NonClosingBacklog(depth)
    since = CursorKey.opening(datetime(2026, 1, 1, tzinfo=UTC))

    assert oldest_unsent(backlog, since, since.at + timedelta(days=1)) is None  # type: ignore[arg-type]

    # One read per page, then the settling read that finds the position unmoved.
    assert backlog.limits == [100] * (pages + 1)
    assert backlog.hydrated == pages + 1


def test_oldest_unsent_answers_none_with_nothing_after_the_cursor_and_writes_nothing(tmp_path: Path) -> None:
    hub, graph = trace_hub(tmp_path)
    transitioned_and_stopped(hub, graph, 1)
    store = trace_store(hub)
    newest = store.newest_cursor()
    since = CursorKey.opening(hub.clock.now() + timedelta(hours=1))

    assert oldest_unsent(store, since, since.at + timedelta(hours=1)) is None
    assert store.newest_cursor() == newest


def test_cursor_rows_append_and_the_newest_is_the_position(tmp_path: Path) -> None:
    hub, _graph = trace_hub(tmp_path)
    store = trace_store(hub)
    now = hub.clock.now()
    assert store.newest_cursor() is None

    start = TraceCheckpoint(CursorKey.opening(now), 0, now)
    store.append_cursor(start)
    advanced = TraceCheckpoint(CursorKey(now, "ch_1", 2, "dec_1"), 7, now + timedelta(seconds=5))
    store.append_cursor(advanced)

    assert store.newest_cursor() == advanced


def test_the_export_latch_reads_the_newer_of_the_two_kinds(tmp_path: Path) -> None:
    hub, _graph = trace_hub(tmp_path)
    store = trace_store(hub)
    assert store.newest_export_latch() is None

    def record(kind: HubEventKind, seconds: int) -> None:
        hub_event_log_of(hub.engine).record(
            kind=kind,
            message=kind,
            detail=None,
            at=hub.clock.now() + timedelta(seconds=seconds),
        )

    record("trace-export-failed", 1)
    assert store.newest_export_latch() == "trace-export-failed"
    record("trace-config-rejected", 2)
    record("trace-export-recovered", 3)
    assert store.newest_export_latch() == "trace-export-recovered"
