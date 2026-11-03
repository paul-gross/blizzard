"""The ``flow-changed`` frame (component tier): every write of a flow fact — a submission, a turn's start and end, a
landing, a closure, and a lever's withdrawal — publishes exactly one frame per fact once its transaction committed. A
write that rolled back publishes nothing, and the boot's recovery, run before any stream opens, publishes nothing. A
visit a stop, restart, or completion moved after the reconcile selected it writes no submission, fact, or frame: the
submission re-reads the chunk's standing on the token its chunk's lock returned, and writes on that token."""

from __future__ import annotations

import json
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import insert, select

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.hub_event_types import HubEventType
from blizzard.hub.delivery.flow_changes import publish_flow_facts
from blizzard.hub.delivery.flow_driver import FlowDriver, FlowLineStores
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction
from blizzard.hub.forge.budget_notices import BudgetNoticeDrain
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_lifecycle_store
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_record_store import ChunkRecordStore
from blizzard.hub.store.internal.flow_store import FlowStore
from blizzard.hub.store.transactions import HubTransactions
from tests.github_double import DoubleRepo, GitHubDouble
from tests.support import (
    HubHarness,
    RepositorySeed,
    build_hub,
    default_tenant,
    emitted_events,
    hub_scoped,
    hub_store_connections,
    seed_work_item,
    stamp,
)

pytestmark = pytest.mark.component

_WIDGET = "acme/widget"
_GADGET = "acme/gadget"
_SUBMITTED = "c" * 40

_GRAPH = """
name: land-pass
entry: deliver
nodes:
  deliver:
    executor: hub
    action: land
    judgement:
      choices:
        landed: {description: landed., to: done}
        conflict: {description: conflict., to: done}
        inherited-failure: {description: inherited., to: done}
        failure: {description: failure., to: done}
"""

_REPOSITORIES = tuple(
    RepositorySeed(
        forge_api_url="http://forge.one",
        owner="acme",
        repo=name,
        base_branch="main",
        secret_name="fixture-forge-token",
    )
    for name in ("widget", "gadget")
)

type _Fact = tuple[str, str, str, str, str | None]


def _hub(tmp_path: Path) -> HubHarness:
    double = GitHubDouble()
    for repo in (_WIDGET, _GADGET):
        double.repos[repo] = DoubleRepo(heads={"feat/x": _SUBMITTED})
    return build_hub(tmp_path, repositories=_REPOSITORIES, forges=double.forges)


def _chunk(hub: HubHarness, *repos: str) -> str:
    """A chunk standing at ``deliver`` with one commit on each of ``repos``, not yet submitted."""
    minted = hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH})
    assert minted.status_code == 201, minted.text
    item = seed_work_item(
        hub_store_connections(hub.engine),
        graph_id=minted.json()["graph_id"],
        author=WorkItemAuthor.user("u_1"),
        at=hub.clock.now(),
    )
    chunk_id = f"ch_{item.ref}"
    with hub.engine.begin() as conn:
        for index, repo in enumerate(repos):
            conn.execute(
                insert(s.artifacts).values(
                    stamp(
                        s.artifacts,
                        artifact_id=f"ar_{chunk_id}_{index}",
                        chunk_id=chunk_id,
                        node_id="nd_build",
                        node_name="build",
                        epoch=0,
                        name=f"w{index}",
                        kind="git_commit",
                        data=f"feat/x:{_SUBMITTED}",
                        repo=repo,
                        produced_at=hub.clock.now(),
                        seq=index + 1,
                    )
                )
            )
    hub.stand_at(chunk_id, "deliver")
    return chunk_id


def _frames(hub: HubHarness, *, since: int = 0) -> list[_Fact]:
    """Every ``flow-changed`` frame published after ``since``, as ``(flow, kind, submission, chunk, outcome)``."""
    frames = [
        json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == HubEventType.FLOW_CHANGED
    ]
    return [(f["flow_id"], f["kind"], f["submission_id"], f["chunk_id"], f.get("outcome")) for f in frames]


def _facts(hub: HubHarness, chunk_id: str, *, after: int = 0) -> list[_Fact]:
    """The chunk's flow facts with an id above ``after``, in write order, as :func:`_frames` names a frame."""
    table = s.flow_facts
    with hub.engine.connect() as conn:
        rows = conn.execute(
            hub_scoped(select(table)).where(table.c.chunk_id == chunk_id, table.c.id > after).order_by(table.c.id)
        ).all()
    return [(r.flow_id, r.kind, r.submission_id, r.chunk_id, r.outcome) for r in rows]


def _newest_fact_id(hub: HubHarness) -> int:
    with hub.engine.connect() as conn:
        return max(conn.execute(hub_scoped(select(s.flow_facts.c.id))).scalars(), default=0)


def _kinds(facts: list[_Fact]) -> list[tuple[str, str | None]]:
    return [(kind, outcome) for _, kind, _, _, outcome in facts]


def test_a_landed_turn_publishes_one_frame_per_flow_fact_it_wrote(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    chunk_id = _chunk(hub, _WIDGET, _GADGET)

    assert hub.turn_outcome(chunk_id) == "landed"

    facts = _facts(hub, chunk_id)
    assert _kinds(facts) == [
        *[("submitted", None)] * 2,
        *[("turn-started", None)] * 2,
        *[("landed", None)] * 2,
        *[("turn-ended", "landed")] * 2,
        *[("withdrawn", "landed")] * 2,
    ]
    assert _frames(hub) == facts, "one frame per fact, each published in write order"


def test_a_lever_publishes_the_withdrawn_fact_of_each_waiting_submission(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    chunk_id = _chunk(hub, _WIDGET)
    assert hub.services.flow_lines.reconcile(0).submitted == 1
    since, after = hub.events.latest_id(), _newest_fact_id(hub)

    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code == 202

    withdrawn = _facts(hub, chunk_id, after=after)
    assert _kinds(withdrawn) == [("withdrawn", "stopped")]
    assert _frames(hub, since=since) == withdrawn


_LEVERS: dict[str, dict[str, Any]] = {
    "stop": {"by": "operator"},
    "restart": {"node": "deliver"},
    "complete": {"by": "operator"},
}
#: The status each lever leaves the chunk in.
_LEVER_STATUS = {"stop": ChunkStatus.STOPPED, "restart": ChunkStatus.DELIVERING, "complete": ChunkStatus.DONE}


def _standing_of(hub: HubHarness, chunk_id: str) -> tuple[ChunkStatus, int]:
    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    return facts.status(), facts.latest_epoch() or 0


@pytest.mark.parametrize("lever", list(_LEVERS))
def test_a_lever_committed_after_the_reconcile_selected_a_visit_leaves_it_unsubmitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lever: str
) -> None:
    hub = _hub(tmp_path)
    chunk_id = _chunk(hub, _WIDGET)
    selected_epoch = _standing_of(hub, chunk_id)[1]
    since = hub.events.latest_id()
    submit = FlowLineReconciler.submit
    pulled: list[int] = []
    outcomes: list[object] = []

    def lever_then_submit(self: FlowLineReconciler, *args: Any, **kwargs: Any) -> Any:
        pulled.append(hub.client.post(f"/api/chunks/{chunk_id}/{lever}", json=_LEVERS[lever]).status_code)
        try:
            outcomes.append(submitted := submit(self, *args, **kwargs))
        except Exception as exc:
            outcomes.append(exc)
            raise
        return submitted

    monkeypatch.setattr(FlowLineReconciler, "submit", lever_then_submit)
    result = hub.services.flow_lines.reconcile(0)
    publish_flow_facts(hub.services.events, result.written)

    assert pulled == [202]
    assert outcomes == [None], "the submit wrote nothing and raised nothing"
    status, epoch = _standing_of(hub, chunk_id)
    assert status is _LEVER_STATUS[lever]
    assert (epoch > selected_epoch) is (lever == "restart")
    assert result.submitted == 0
    assert FlowStore(hub_store_connections(hub.engine), hub.clock).submissions_of(chunk_id) == []
    assert _facts(hub, chunk_id) == []
    assert [frame for frame in _frames(hub, since=since) if frame[3] == chunk_id] == []


class _RecordingUnit:
    def __init__(self, unit: Transaction, locked: list[tuple[list[str], LockedTransaction]]) -> None:
        self._unit = unit
        self._locked = locked

    def lock_chunks(self, chunk_ids: Sequence[str]) -> LockedTransaction:
        locked = self._unit.lock_chunks(chunk_ids)
        self._locked.append((list(chunk_ids), locked))
        return locked


def test_a_submission_reads_and_writes_on_the_token_its_chunk_lock_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = _hub(tmp_path)
    chunk_id = _chunk(hub, _WIDGET)
    locked: list[tuple[list[str], LockedTransaction]] = []
    calls: list[tuple[str, object]] = []
    begin = HubTransactions.begin

    @contextmanager
    def recording_begin(self: HubTransactions) -> Generator[_RecordingUnit]:
        with begin(self) as unit:
            yield _RecordingUnit(unit, locked)

    def recording(owner: type, name: str) -> None:
        method: Callable[..., Any] = getattr(owner, name)

        def record(self: object, chunk: str, *args: Any, **kwargs: Any) -> Any:
            if chunk == chunk_id:
                calls.append((name, kwargs.get("transaction")))
            return method(self, chunk, *args, **kwargs)

        monkeypatch.setattr(owner, name, record)

    monkeypatch.setattr(HubTransactions, "begin", recording_begin)
    for owner, name in [
        (ChunkFactsStore, "load_facts"),
        (ChunkRecordStore, "get"),
        (FlowStore, "submission_for_visit"),
        (FlowStore, "submit"),
    ]:
        recording(owner, name)

    assert hub.services.flow_lines.reconcile(0).submitted == 1

    [(chunk_ids, token)] = locked
    assert chunk_ids == [chunk_id]
    assert calls == [
        ("submission_for_visit", None),
        ("load_facts", token),
        ("get", token),
        ("submission_for_visit", token),
        ("submit", token),
    ]


def test_a_lever_whose_write_rolled_back_publishes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = _hub(tmp_path)
    chunk_id = _chunk(hub, _WIDGET)
    assert hub.services.flow_lines.reconcile(0).submitted == 1
    since, after = hub.events.latest_id(), _newest_fact_id(hub)

    def failing(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the write fails after its withdrawal")

    monkeypatch.setattr(chunk_lifecycle_store, "enqueue_close_intents", failing)
    with pytest.raises(RuntimeError):
        hub.client.post(f"/api/chunks/{chunk_id}/complete", json={"by": "operator"})

    assert _facts(hub, chunk_id, after=after) == []
    assert _frames(hub, since=since) == []


def test_a_turn_end_that_rolled_back_publishes_no_frame_for_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = _hub(tmp_path)
    chunk_id = _chunk(hub, _WIDGET)
    end = FlowStore.end_turn_locked

    def end_then_fail(self: FlowStore, *args: Any, **kwargs: Any) -> Any:
        end(self, *args, **kwargs)
        raise RuntimeError("the routing fails after the turn's end")

    monkeypatch.setattr(FlowStore, "end_turn_locked", end_then_fail)
    hub.take_turns()

    facts = _facts(hub, chunk_id)
    assert ("turn-ended", "landed") not in _kinds(facts), "the turn's end rolled back with its routing"
    assert ("landed", None) in _kinds(facts), "the landing committed on its own"
    assert _frames(hub) == facts


def _turning(hub: HubHarness, *repos: str) -> str:
    """A chunk whose turn is open with no task answering for it — taken past the driver, so nothing was published."""
    chunk_id = _chunk(hub, *repos)
    lines = hub.services.flow_lines
    assert lines.reconcile(0).submitted == 1
    assert len(lines.admit(0, hub.clock.now())) == 1
    return chunk_id


def test_the_sweep_publishes_the_facts_of_a_turn_it_ends_interrupted(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    chunk_id = _turning(hub, _WIDGET)
    since, after = hub.events.latest_id(), _newest_fact_id(hub)
    stores = FlowLineStores(
        corpus=hub.hub_services.hub_scoped_reads.corpus,
        tenants=lambda: hub.hub_services.tenants.open_tenants.every().keys(),
        lines=lambda tenant: hub.open_services(tenant).flow_lines,
        turns=lambda tenant: hub.open_services(tenant).turns,
        publishers=lambda tenant: hub.open_services(tenant).events,
        notices=lambda tenant: BudgetNoticeDrain(
            hub.open_services(tenant).forge_budgets, hub.open_services(tenant).event_log
        ),
    )
    driver = FlowDriver(stores, hub.clock)

    for _ in range(2):
        hub.clock.advance(timedelta(seconds=1))
        driver.tick(stores, hub.clock, running=frozenset())

    written = _facts(hub, chunk_id, after=after)
    assert _kinds(written)[0] == ("turn-ended", "interrupted")
    assert _frames(hub, since=since) == written


def test_boot_recovery_publishes_none_of_the_facts_it_writes(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    chunk_id = _turning(hub, _WIDGET)
    since, after = hub.events.latest_id(), _newest_fact_id(hub)

    assert hub.flow_driver.recover_turns(default_tenant(hub.engine)) == 1

    assert _kinds(_facts(hub, chunk_id, after=after)) == [("turn-ended", "interrupted")]
    assert _frames(hub, since=since) == []
