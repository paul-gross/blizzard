"""A hub node naming an action takes one pass per turn in place of its ``run:`` steps: the delivering actions record
through the hub's recorders, the pass is bounded, an escalation routes nothing, and a node an action left waiting
takes its next turn once its poll is due (component tier)."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert, select

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.delivery.actions.base import IHubAction
from blizzard.hub.domain.actions.effects import ActionEscalation, IEffectSink, Marker, Pass
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.chunk.model import HubNodePollFact, WorkItemAuthor
from blizzard.hub.domain.config.secrets import SecretName, SecretRetired
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.graph.actions import ActionSpec
from blizzard.hub.forge.landing import VisitForges
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.run_context_store import RunContextStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import (
    TEST_HUB_ACTIONS,
    HubHarness,
    build_hub,
    create_repositories,
    default_project,
    fixture_repository,
    hub_scoped,
    hub_store_connections,
    seed_work_item,
    stamp,
)

pytestmark = pytest.mark.component

_EPOCH = 1
_SCOPE = "blizzard"


def _spec(name: str, *answers: str, lands: bool = False) -> ActionSpec:
    return ActionSpec(
        name=name,
        args=frozenset(),
        required_args=frozenset(),
        answers=frozenset(answers),
        answers_from_arg=None,
        lands=lands,
    )


class _Answers:
    """An in-process action answering a fixed pass."""

    def __init__(self, spec: ActionSpec, answer: Pass) -> None:
        self.spec = spec
        self._answer = answer

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        return self._answer


class _Sleeps:
    """Waits on the loop far past any bound a test sets."""

    def __init__(self, spec: ActionSpec) -> None:
        self.spec = spec

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        await asyncio.sleep(60)
        return Pass(choice="done")


class _Blocks:
    """Holds the loop in synchronous work until released."""

    def __init__(self, spec: ActionSpec, release: threading.Event) -> None:
        self.spec = spec
        self._release = release

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        self._release.wait(10)
        return Pass(choice="done")


class _Raises:
    def __init__(self, spec: ActionSpec) -> None:
        self.spec = spec

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        await effects.record(Marker("before-the-raise", "x"))
        raise RuntimeError("the pass broke")


def _hub(tmp_path: Path, *extra: IHubAction, **kwargs: object) -> HubHarness:
    actions = HubActions.of([*TEST_HUB_ACTIONS.implementations.values(), *extra])
    return build_hub(tmp_path, actions=actions, **kwargs)  # type: ignore[arg-type]


def _graph(action: str, choices: tuple[str, ...], *, extra: str = "") -> str:
    # Every hub node authors `failure`.
    authored = dict.fromkeys((*choices, "failure"))
    routed = "\n".join(f"        {c}: {{description: {c}., to: done}}" for c in authored)
    return f"""
name: action-pass
entry: deliver
nodes:
  deliver:
    executor: hub
    action: {action}
{extra}    judgement:
      choices:
{routed}
"""


def _at_deliver(hub: HubHarness, yaml: str, *, run_context: bool = False) -> str:
    """Mint ``yaml`` and seed a resting chunk on it, standing at its hub entry node."""
    minted = hub.client.post("/api/graphs", json={"definition_yaml": yaml})
    assert minted.status_code == 201, minted.text
    connections = hub_store_connections(hub.engine)
    item = seed_work_item(
        WorkItemStore(connections),
        graph_id=minted.json()["graph_id"],
        author=WorkItemAuthor.user("u_1"),
        at=hub.clock.now(),
    )
    if run_context:
        RunContextStore(connections).record(
            item.work_item_id,
            RunContext(
                project_id=default_project(hub.engine), routine_name="nightly", scope_slug=_SCOPE, mode="dry_run"
            ),
        )
    chunk_id = f"ch_{item.ref}"
    hub.promote(chunk_id)
    # A restart onto the hub node stands the chunk there, delivering, at the hub-owned epoch _EPOCH.
    restarted = hub.client.post(f"/api/chunks/{chunk_id}/restart", json={"node": "deliver"})
    assert restarted.status_code == 202, restarted.text
    return chunk_id


def _visit(hub: HubHarness, chunk_id: str) -> str | None:
    """The outcome of the turn one tick takes for the chunk, or ``None`` when it takes none."""
    (result,) = hub.take_turns()
    started = [t for t in result.started if t.submission.chunk_id == chunk_id]
    if not started:
        return None
    (turn,) = started
    with hub.engine.connect() as conn:
        return conn.execute(
            hub_scoped(select(s.hub_turns.c.outcome).where(s.hub_turns.c.turn_id == turn.turn.turn_id))
        ).scalar_one()


def _artifact(hub: HubHarness, chunk_id: str, name: str) -> str | None:
    return next((a.data for a in hub.services.chunks.artifacts.load_artifacts(chunk_id) if a.name == name), None)


def _record_asset(hub: HubHarness, chunk_id: str, name: str, content: str) -> None:
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None
    node = graph.node_by_name("deliver")
    assert node is not None
    assert hub.services.hub_markers.record_marker(
        chunk_id, node_id=node.node_id, node_name="deliver", epoch=_EPOCH, name=name, content=content
    )


def _polls(hub: HubHarness, chunk_id: str) -> list[HubNodePollFact]:
    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    return facts.hub_node_poll_history(node_id=_node_id(hub, chunk_id), epoch=_EPOCH)


def _findings(hub: HubHarness) -> int:
    with hub.engine.begin() as conn:
        return len(conn.execute(hub_scoped(select(s.findings))).all())


def _events(hub: HubHarness, chunk_id: str, kind: str) -> list[object]:
    return [e for e in hub.services.chunks.events.list_events(chunk_id=chunk_id) if e.kind == kind]


# --- record-findings ---------------------------------------------------------


_DEFERRED = {
    "ref": "F1",
    "disposition": "deferred",
    "severity": "should-fix",
    "scope": "blizzard",
    "class": "correctness",
    "locus": "a.py:1",
    "summary": "s",
}


def test_record_findings_records_the_chunks_review_delta(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    chunk_id = _at_deliver(hub, _graph("record-findings", ("recorded", "invalid", "failure")))
    _record_asset(hub, chunk_id, "review-finding-delta", json.dumps({"entries": [_DEFERRED]}))

    assert _visit(hub, chunk_id) == "recorded"
    assert _findings(hub) == 1
    assert _artifact(hub, chunk_id, "review-findings-failure") is None


def test_record_findings_without_a_delta_answers_invalid_with_the_recorders_explanation(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    chunk_id = _at_deliver(hub, _graph("record-findings", ("recorded", "invalid", "failure")))
    route = hub.client.post(
        f"/api/chunks/{chunk_id}/review-findings-delivery?node_id={_node_id(hub, chunk_id)}&epoch={_EPOCH}"
    ).json()

    assert _visit(hub, chunk_id) == "invalid"
    assert route["outcome"] == "invalid"
    assert _artifact(hub, chunk_id, "review-findings-failure") == route["detail"]
    assert _findings(hub) == 0


def _node_id(hub: HubHarness, chunk_id: str) -> str:
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None
    node = graph.node_by_name("deliver")
    assert node is not None
    return node.node_id


# --- garden-deliver ----------------------------------------------------------


def _garden_graph() -> str:
    return _graph(
        "garden-deliver",
        ("recorded", "invalid", "invalid-proposals", "failure"),
        extra="    with: {delta: delta, proposals: [docket]}\n",
    )


def _seed_scope(hub: HubHarness) -> None:
    with hub.engine.begin() as conn:
        conn.execute(insert(s.scopes).values(stamp(s.scopes, slug=_SCOPE, description="", created_at=hub.clock.now())))


def _garden_delta(findings: list[dict[str, str]]) -> str:
    return json.dumps({"scope": _SCOPE, "revisions": {}, "measurement": None, "findings": findings})


def test_garden_deliver_records_its_named_delta(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    _seed_scope(hub)
    chunk_id = _at_deliver(hub, _garden_graph(), run_context=True)
    _record_asset(
        hub, chunk_id, "delta", _garden_delta([{"op": "add", "class": "c", "locus": "a.py:1", "summary": "s"}])
    )

    assert _visit(hub, chunk_id) == "recorded"
    assert _findings(hub) == 1


def test_garden_deliver_with_a_malformed_delta_answers_invalid_naming_it(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    _seed_scope(hub)
    chunk_id = _at_deliver(hub, _garden_graph(), run_context=True)
    _record_asset(hub, chunk_id, "delta", "not valid json")

    assert _visit(hub, chunk_id) == "invalid"
    failure = _artifact(hub, chunk_id, "garden-delivery-failure")
    assert failure is not None
    assert "delta" in failure
    assert _findings(hub) == 0


def test_garden_deliver_with_a_missing_delta_answers_invalid(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    _seed_scope(hub)
    chunk_id = _at_deliver(hub, _garden_graph(), run_context=True)

    assert _visit(hub, chunk_id) == "invalid"
    assert _artifact(hub, chunk_id, "garden-delivery-failure") is not None


def test_garden_deliver_with_a_malformed_docket_answers_invalid_proposals_and_records_the_marker(
    tmp_path: Path,
) -> None:
    hub = _hub(tmp_path)
    _seed_scope(hub)
    chunk_id = _at_deliver(hub, _garden_graph(), run_context=True)
    _record_asset(hub, chunk_id, "delta", _garden_delta([]))
    _record_asset(hub, chunk_id, "docket", "not valid json")

    assert _visit(hub, chunk_id) == "invalid-proposals"
    failure = _artifact(hub, chunk_id, "garden-delivery-failure")
    assert failure is not None
    assert "docket" in failure


def test_garden_deliver_citing_an_unknown_finding_answers_invalid_proposals(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    _seed_scope(hub)
    chunk_id = _at_deliver(hub, _garden_graph(), run_context=True)
    _record_asset(hub, chunk_id, "delta", _garden_delta([]))
    unknown = Id.mint_at(IdPrefix.FINDING, hub.clock.now()).value
    docket = [{"ref": "p1", "class": "remediate", "title": "t", "body": "b", "findings": [unknown]}]
    _record_asset(hub, chunk_id, "docket", json.dumps(docket))

    assert _visit(hub, chunk_id) == "invalid-proposals"
    assert _findings(hub) == 0


def test_garden_deliver_citing_an_unknown_add_ref_answers_invalid_proposals(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    _seed_scope(hub)
    chunk_id = _at_deliver(hub, _garden_graph(), run_context=True)
    _record_asset(hub, chunk_id, "delta", _garden_delta([]))
    docket = [{"ref": "p1", "class": "remediate", "title": "t", "body": "b", "findings": ["ghost-ref"]}]
    _record_asset(hub, chunk_id, "docket", json.dumps(docket))

    assert _visit(hub, chunk_id) == "invalid-proposals"


def test_garden_deliver_with_no_run_context_answers_invalid(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    chunk_id = _at_deliver(hub, _garden_graph())

    assert _visit(hub, chunk_id) == "invalid"
    assert _artifact(hub, chunk_id, "garden-delivery-failure") == f"no run context for chunk {chunk_id}"


# --- the pass's bounds -------------------------------------------------------


def test_an_overrunning_pass_answers_pending_and_records_the_overrun(tmp_path: Path) -> None:
    hub = _hub(tmp_path, _Sleeps(_spec("test.slow", "done", "pending")), action_pass_seconds=0.2)
    chunk_id = _at_deliver(hub, _graph("test.slow", ("done",)))

    assert _visit(hub, chunk_id) == "pending"
    assert len(_polls(hub, chunk_id)) == 1
    assert len(_events(hub, chunk_id, "hub-action-overran")) == 1


def test_an_overrunning_pass_of_an_action_that_cannot_wait_answers_failure(tmp_path: Path) -> None:
    hub = _hub(tmp_path, _Sleeps(_spec("test.slow", "done", "failure")), action_pass_seconds=0.2)
    chunk_id = _at_deliver(hub, _graph("test.slow", ("done", "failure")))

    assert _visit(hub, chunk_id) == "failure"
    assert len(_events(hub, chunk_id, "hub-action-overran")) == 1


def test_a_pass_stuck_in_synchronous_work_is_cut_off_at_the_callers_deadline_routing_nothing(tmp_path: Path) -> None:
    release = threading.Event()
    hub = _hub(
        tmp_path,
        _Blocks(_spec("test.stuck", "done", "pending"), release),
        action_pass_seconds=0.2,
        action_pass_grace=timedelta(seconds=0.3),
    )
    chunk_id = _at_deliver(hub, _graph("test.stuck", ("done",)))
    try:
        started = time.monotonic()
        result = _visit(hub, chunk_id)
        waited = time.monotonic() - started
    finally:
        release.set()

    assert result == "cut-off"
    assert waited < 5
    assert _polls(hub, chunk_id) == []
    assert len(_events(hub, chunk_id, "hub-action-overran")) == 1
    assert hub.services.flows.open_turns() == []


def test_an_exception_escaping_a_pass_answers_failure_with_its_traceback(tmp_path: Path) -> None:
    hub = _hub(tmp_path, _Raises(_spec("test.raises", "done", "failure")))
    chunk_id = _at_deliver(hub, _graph("test.raises", ("done", "failure")))

    assert _visit(hub, chunk_id) == "failure"
    log = _artifact(hub, chunk_id, "hub-log.test.raises")
    assert log is not None
    assert "RuntimeError: the pass broke" in log
    assert _artifact(hub, chunk_id, "before-the-raise") == "x"


# --- answers -----------------------------------------------------------------


def test_an_escalation_escalates_once_at_the_next_epoch_and_routes_nothing(tmp_path: Path) -> None:
    escalation = ActionEscalation(EscalationCause.SPEND_CAP, "a person must settle this")
    hub = _hub(tmp_path, _Answers(_spec("test.escalates", "done"), Pass(choice=None, escalation=escalation)))
    chunk_id = _at_deliver(hub, _graph("test.escalates", ("done",)))

    assert _visit(hub, chunk_id) == "escalated"
    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    assert facts.transitions == []
    with hub.engine.begin() as conn:
        rows = conn.execute(hub_scoped(select(s.escalations).where(s.escalations.c.chunk_id == chunk_id))).all()
    assert [(row.epoch, row.cause) for row in rows] == [(_EPOCH + 1, "spend-cap")]
    # The escalation opened a fresh epoch, so this visit is never driven again.
    assert _visit(hub, chunk_id) is None


def test_a_reserved_wait_writes_the_actions_next_poll_at(tmp_path: Path) -> None:
    # `build_hub`'s clock stands at 2026-07-13 00:00 UTC.
    later = datetime(2026, 7, 13, 0, 7, tzinfo=UTC)
    hub = _hub(
        tmp_path,
        _Answers(_spec("test.hints", "done", "pending"), Pass(choice="pending", next_poll_at=later)),
    )
    chunk_id = _at_deliver(hub, _graph("test.hints", ("done",)))

    assert _visit(hub, chunk_id) == "pending"
    assert [poll.next_poll_at for poll in _polls(hub, chunk_id)] == [later]


_SEQUENCE_GRAPH = """
name: action-sequence
entry: build
nodes:
  build:
    executor: runner
    prompt: Build the change.
    judgement:
      prompt: Assess the build.
      choices:
        pass: {description: Green., to: deliver}
  deliver:
    executor: hub
    action: test.sequence
    with: {choices: [pending, done]}
    poll_interval: 30
    judgement:
      choices:
        done: {description: Done., to: done}
        failure: {description: Failed., to: build}
"""


def test_a_node_waiting_on_an_action_takes_its_next_turn_once_its_poll_is_due(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    chunk_id = _at_deliver(hub, _SEQUENCE_GRAPH)

    assert _visit(hub, chunk_id) == "pending"
    assert _visit(hub, chunk_id) is None
    advanced = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance").json()
    assert (advanced["ran"], advanced["outcome_choice"]) == (False, None)
    assert advanced["detail"].startswith("pending — next poll at ")

    hub.clock.advance(timedelta(seconds=31))
    assert _visit(hub, chunk_id) == "done"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"


# --- secrets -----------------------------------------------------------------


@pytest.mark.parametrize(("lands", "choice"), [(True, "failure"), (False, "done")])
def test_a_repository_secret_is_revealed_only_for_an_action_that_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lands: bool, choice: str
) -> None:
    spec = _spec("test.lands", "done", "failure", lands=lands)
    hub = _hub(tmp_path, _Answers(spec, Pass(choice="done")), repositories=())
    create_repositories(hub.client, [fixture_repository("widget", "http://forge.example", owner="acme")])
    revealed: list[str] = []

    def retired(name: SecretName):  #
        revealed.append(name.value)
        raise SecretRetired(name.value)

    monkeypatch.setattr(hub.services.forges._secrets, "reveal", retired)
    chunk_id = _at_deliver(hub, _graph("test.lands", ("done", "failure")))
    _record_commit(hub, chunk_id, "acme/widget")

    assert _visit(hub, chunk_id) == choice
    assert bool(revealed) is lands
    assert (_artifact(hub, chunk_id, "hub-log.repository-secret-unavailable") is not None) is lands


def _record_commit(hub: HubHarness, chunk_id: str, repo: str) -> None:
    with hub.engine.begin() as conn:
        conn.execute(
            insert(s.artifacts).values(
                stamp(
                    s.artifacts,
                    artifact_id=f"ar_{chunk_id}_commit",
                    chunk_id=chunk_id,
                    node_id="nd_build",
                    node_name="build",
                    epoch=0,
                    name="w",
                    kind="git_commit",
                    data=f"feat/x:{'a' * 40}",
                    repo=repo,
                    forge=None,
                    produced_at=hub.clock.now(),
                    seq=1,
                )
            )
        )
