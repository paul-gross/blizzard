"""The errand-runs read and the cadence mint on a production-wired hub (component tier).

The read answers each errand's newest marked run with its closure and its holder — a terminal holder kept, a grouped
run followed into its survivor, a lost holder read as live — and nothing a hand-started run or another chore minted.
The mint plans as a hand-started run plans, re-reads the errand's last run under its lock and writes nothing when it
moved, lands a promoting chore's run at the ready queue's tail beside an operator's promote, and one errand's refusal
leaves another's mint untouched."""

from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.run_mode import RunMode
from blizzard.foundation.work_items import WorkItemClosure
from blizzard.hub.domain.cadence.errands import Errand, ErrandRun, RunOutcome, errands
from blizzard.hub.domain.cadence.mint import MintedErrand
from blizzard.hub.domain.cadence.model import AttentionFacts, Cadence, CadenceShape, ChoreShape
from blizzard.hub.domain.cadence.services import CadenceAttention
from blizzard.hub.domain.chunk.model import Chunk, ErrandMark, WorkItemAuthor, WorkItemAuthorKind
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.routines import Routine
from blizzard.hub.domain.garden.runs.run import RoutineRetiredError
from blizzard.hub.domain.garden.scopes import Scope, ScopeSlug
from blizzard.hub.store import schema as s
from tests.support.scaffolding import HubHarness, build_hub, default_project, hub_scoped, project_record

_CTX = ChangeContext(actor="operator", door=Door.API)


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path)


def _routine(hub: HubHarness, slug: str, *linked: str) -> Routine:
    graph = hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )
    project = project_record(hub, default_project(hub.engine))
    routine = hub.services.routine_authoring.create(
        project=project, ctx=_CTX, slug=slug, graph_name=graph.name, default_scope_slug=ScopeSlug.parse(slug)
    )
    for scope_slug in linked:
        scope = hub.services.scope_registry.ensure(project, ScopeSlug.parse(scope_slug), _CTX)
        routine = hub.services.routine_scope_membership.link(routine, scope, _CTX)
    return routine


def _cadence(hub: HubHarness, *chores: ChoreShape) -> Cadence:
    return hub.services.cadence_authoring.create(
        CadenceShape(slug="nightly", spacing="1h", chores=chores),
        project=project_record(hub, default_project(hub.engine)),
        ctx=_CTX,
        may_admit=True,
    )


def _facts(hub: HubHarness, cadence: Cadence) -> AttentionFacts:
    attention = CadenceAttention(
        routines=hub.services.routines,
        routine_scopes=hub.services.routine_scopes,
        scopes=hub.services.scopes,
        graphs=hub.services.graphs,
    )
    return attention.facts([cadence])[cadence.project_id]


def _errands(hub: HubHarness, cadence: Cadence) -> dict[tuple[str, str], Errand]:
    return {(errand.chore.spec.key, errand.scope_slug): errand for errand in errands(cadence, _facts(hub, cadence))}


def _mint(hub: HubHarness, cadence: Cadence, errand: Errand, *, judged: ErrandRun | None = None) -> MintedErrand:
    minted = _try_mint(hub, cadence, errand, judged=judged)
    assert minted is not None
    return minted


def _try_mint(hub: HubHarness, cadence: Cadence, errand: Errand, *, judged: ErrandRun | None) -> MintedErrand | None:
    inputs = hub.services.errand_planning.inputs([(errand, _facts(hub, cadence))])
    return hub.services.cadence_mint.mint(errand, judged=judged, inputs=inputs[errand.key])


def _last(hub: HubHarness, errand: Errand) -> ErrandRun | None:
    return hub.services.errand_runs.last_runs([errand.key]).get(errand.key)


def _chunk(hub: HubHarness, chunk_id: str) -> Chunk:
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    return chunk


def _status(hub: HubHarness, chunk_id: str) -> ChunkStatus:
    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    return facts.status()


def _work_items(hub: HubHarness) -> int:
    with hub.engine.connect() as conn:
        return conn.execute(hub_scoped(sa.select(sa.func.count()).select_from(s.work_items))).scalar_one()


# --- the errand-runs read ---


def test_an_errand_never_run_has_no_last_run_and_others_runs_are_not_its(hub: HubHarness) -> None:
    routine = _routine(hub, "sweep", "runner")
    cadence = _cadence(hub, ChoreShape("every", "sweep", scopes="all"), ChoreShape("one", "sweep", scopes=["runner"]))
    found = _errands(hub, cadence)
    scope = hub.services.scopes.get(routine.project_id, "runner")
    assert scope is not None
    hub.services.routine_run.run(routine, scope=scope, mode=RunMode.FULL, note=None, author=WorkItemAuthor.user("u"))
    _mint(hub, cadence, found[("one", "runner")])

    assert _last(hub, found[("every", "runner")]) is None
    assert _last(hub, found[("every", "sweep")]) is None
    assert _last(hub, found[("one", "runner")]) is not None


def test_the_newest_marked_run_is_the_last_and_a_stopped_holder_settles_it(hub: HubHarness) -> None:
    _routine(hub, "sweep")
    cadence = _cadence(hub, ChoreShape("plain", "sweep"))
    (errand,) = _errands(hub, cadence).values()
    first = _mint(hub, cadence, errand)
    stopped_at = hub.clock.now()
    hub.services.stop.stop(_chunk(hub, first.chunk.chunk_id), by="operator")

    settled = _last(hub, errand)
    assert settled is not None
    assert settled.work_item_id == first.item.work_item_id
    assert settled.run.outcome() is RunOutcome.CANCELLED
    assert settled.run.holder is not None
    assert settled.run.holder.completed_at == stopped_at

    hub.clock.advance(timedelta(hours=2))
    second = _mint(hub, cadence, errand, judged=settled)
    newest = _last(hub, errand)
    assert newest is not None
    assert newest.work_item_id == second.item.work_item_id
    assert newest.run.minted_at == hub.clock.now()
    assert newest.run.unfinished()


def test_a_deleted_run_reads_withdrawn_and_a_lost_holder_reads_live(hub: HubHarness) -> None:
    _routine(hub, "sweep", "runner")
    cadence = _cadence(hub, ChoreShape("every", "sweep", scopes="all"))
    found = _errands(hub, cadence)
    deleted = _mint(hub, cadence, found[("every", "sweep")])
    lost = _mint(hub, cadence, found[("every", "runner")])
    hub.services.delete.delete(_chunk(hub, deleted.chunk.chunk_id), by="operator")
    with hub.engine.begin() as conn:
        conn.execute(
            hub_scoped(sa.delete(s.chunk_work_refs).where(s.chunk_work_refs.c.chunk_id == lost.chunk.chunk_id))
        )

    withdrawn = _last(hub, found[("every", "sweep")])
    unresolved = _last(hub, found[("every", "runner")])

    assert withdrawn is not None
    assert withdrawn.run.closure is WorkItemClosure.WITHDRAWN
    assert withdrawn.run.outcome() is RunOutcome.CANCELLED
    assert unresolved is not None
    assert unresolved.run.holder is None
    assert unresolved.run.outcome() is RunOutcome.LIVE


def test_runs_grouped_into_one_chunk_each_read_the_survivor_until_it_settles(hub: HubHarness) -> None:
    _routine(hub, "sweep", "runner")
    cadence = _cadence(hub, ChoreShape("every", "sweep", scopes="all", promote=False))
    found = _errands(hub, cadence)
    survivor = _mint(hub, cadence, found[("every", "sweep")])
    folded = _mint(hub, cadence, found[("every", "runner")])
    hub.services.group.group(survivor.chunk.chunk_id, [folded.chunk.chunk_id])

    runs = hub.services.errand_runs.last_runs([errand.key for errand in found.values()])
    assert {run.work_item_id for run in runs.values()} == {survivor.item.work_item_id, folded.item.work_item_id}
    assert all(run.run.unfinished() for run in runs.values())

    hub.services.stop.stop(_chunk(hub, survivor.chunk.chunk_id), by="operator")
    runs = hub.services.errand_runs.last_runs([errand.key for errand in found.values()])
    assert [run.run.outcome() for run in runs.values()] == [RunOutcome.CANCELLED, RunOutcome.CANCELLED]


def test_an_all_chores_errand_joins_when_its_scope_was_linked(hub: HubHarness) -> None:
    routine = _routine(hub, "sweep")
    cadence = _cadence(hub, ChoreShape("every", "sweep", scopes="all"))
    hub.clock.advance(timedelta(days=1))
    project = project_record(hub, routine.project_id)
    later = hub.services.scope_registry.ensure(project, ScopeSlug.parse("later"), _CTX)
    hub.services.routine_scope_membership.link(routine, later, _CTX)

    found = _errands(hub, cadence)

    assert found[("every", "sweep")].joined_at == cadence.chores[0].scopes_set_at
    assert found[("every", "later")].joined_at == hub.clock.now()


# --- the mint ---


def test_a_mint_plans_as_a_hand_run_and_marks_its_run_promoted_or_held(hub: HubHarness) -> None:
    _routine(hub, "sweep", "runner")
    cadence = _cadence(
        hub,
        ChoreShape("every", "sweep", scopes="all", mode="delta", note="look closely"),
        ChoreShape("held", "sweep", scopes=["runner"], promote=False),
    )
    found = _errands(hub, cadence)
    promoted = _mint(hub, cadence, found[("every", "runner")])
    held = _mint(hub, cadence, found[("held", "runner")])

    assert promoted.promoted
    assert promoted.plan.downgraded
    assert promoted.item.title == "sweep run (full)"
    assert promoted.item.body.endswith("This run\nlook closely")
    assert promoted.item.author.kind is WorkItemAuthorKind.CADENCE
    assert _status(hub, promoted.chunk.chunk_id) is ChunkStatus.READY
    assert not held.promoted
    assert _status(hub, held.chunk.chunk_id) is ChunkStatus.NOT_READY
    context = hub.services.run_context.for_chunk(_chunk(hub, held.chunk.chunk_id))
    assert context is not None
    assert context.errand == ErrandMark(cadence_id=cadence.cadence_id, chore_id=cadence.chores[1].chore_id)


def test_a_mint_whose_errand_moved_since_it_was_judged_writes_nothing(hub: HubHarness) -> None:
    _routine(hub, "sweep")
    cadence = _cadence(hub, ChoreShape("plain", "sweep"))
    (errand,) = _errands(hub, cadence).values()
    won = _mint(hub, cadence, errand)
    before = _work_items(hub)

    assert _try_mint(hub, cadence, errand, judged=None) is None
    assert _try_mint(hub, cadence, errand, judged=_last(hub, errand)) is None

    assert _work_items(hub) == before
    newest = _last(hub, errand)
    assert newest is not None
    assert newest.work_item_id == won.item.work_item_id


def test_two_passes_racing_one_errand_mint_it_once(hub: HubHarness) -> None:
    _routine(hub, "sweep")
    cadence = _cadence(hub, ChoreShape("plain", "sweep"))
    (errand,) = _errands(hub, cadence).values()
    inputs = hub.services.errand_planning.inputs([(errand, _facts(hub, cadence))])[errand.key]
    start = threading.Barrier(2)
    outcomes: list[MintedErrand | None] = []

    def race() -> None:
        start.wait()
        outcomes.append(hub.services.cadence_mint.mint(errand, judged=None, inputs=inputs))

    passes = [threading.Thread(target=race) for _ in range(2)]
    for each in passes:
        each.start()
    for each in passes:
        each.join()

    assert sorted(outcome is None for outcome in outcomes) == [False, True]
    assert _work_items(hub) == 1


def test_one_errands_refusal_leaves_anothers_mint_untouched(hub: HubHarness) -> None:
    retiring = _routine(hub, "retiring")
    _routine(hub, "kept")
    cadence = _cadence(hub, ChoreShape("a", "retiring"), ChoreShape("b", "kept"))
    found = _errands(hub, cadence)
    hub.services.routine_lifecycle.retire(retiring, _CTX, by="operator")
    before = _work_items(hub)

    with pytest.raises(RoutineRetiredError):
        _try_mint(hub, cadence, found[("a", "retiring")], judged=None)
    minted = _mint(hub, cadence, found[("b", "kept")])

    assert _work_items(hub) == before + 1
    assert _status(hub, minted.chunk.chunk_id) is ChunkStatus.READY


def test_a_promoting_mint_and_an_operator_promote_each_land_at_their_own_tail(hub: HubHarness) -> None:
    _routine(hub, "sweep", "runner")
    cadence = _cadence(hub, ChoreShape("every", "sweep", scopes="all"))
    found = _errands(hub, cadence)
    hand = hub.services.routine_run.run(
        _routine(hub, "other"),
        scope=_scope(hub, "other"),
        mode=RunMode.FULL,
        note=None,
        author=WorkItemAuthor.user("u"),
    )
    first = _mint(hub, cadence, found[("every", "sweep")])
    hub.services.promote.promote(_chunk(hub, hand.chunk_id))
    last = _mint(hub, cadence, found[("every", "runner")])

    positions = hub.services.chunks.queue.queue_positions([first.chunk.chunk_id, hand.chunk_id, last.chunk.chunk_id])
    assert positions[first.chunk.chunk_id] < positions[hand.chunk_id] < positions[last.chunk.chunk_id]


def _scope(hub: HubHarness, slug: str) -> Scope:
    scope = hub.services.scopes.get(default_project(hub.engine), slug)
    assert scope is not None
    return scope
