"""The environment timeline lays each node step on the environment that held the chunk: the entry step before the
first transition, a step left across graphs, the lease each step ran, and one step looked up in depth."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.graph.model import Choice, Graph, Node
from blizzard.hub.domain.observability.env_timeline import (
    LEASE_SLACK,
    MAX_WINDOW,
    ChunkTimelineFacts,
    EnvSegment,
    EnvTimelineFacts,
    PlacedSegment,
    Roster,
    RouteCreated,
    RouteReleased,
    RouteSpan,
    StepArtifact,
    StepEscalation,
    StepLease,
    StepMigration,
    StepNotFound,
    StepTransition,
    StepUsage,
    StepWindow,
    TimelineRunner,
    env_timeline,
    latest_only,
    newest_per_environment,
    route_spans,
    step_detail,
    step_leases,
    window_start,
)

T0 = datetime(2026, 11, 1, tzinfo=UTC)
ROSTER = Roster(
    runners={"rn": TimelineRunner("r-claude", "hs"), "rn2": TimelineRunner("r-codex", "hs")}, hosts={"hs": "xps"}
)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _node(graph_id: str, node_id: str, name: str, *choices: str) -> Node:
    return Node(
        node_id=node_id,
        graph_id=graph_id,
        name=name,
        executor=Executor.RUNNER,
        prompt=None,
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
        choices=[Choice(choice_id=f"{node_id}-{c}", name=c, description="") for c in choices],
    )


GRAPH = Graph(
    graph_id="gr",
    name="dwf",
    entry_node_id="build",
    nodes=[_node("gr", "build", "build", "pass", "fail"), _node("gr", "verify", "verify", "pass", "fail")],
    edges=[],
    created_at=T0,
)
NEXT_GRAPH = Graph(
    graph_id="gr2",
    name="dwf2",
    entry_node_id="plan",
    nodes=[_node("gr2", "plan", "plan", "pass"), _node("gr2", "ship", "ship", "pass")],
    edges=[],
    created_at=T0,
)


def _route(
    created: float, *, released: float | None = None, runner: str = "rn", envs: tuple[str, ...] = ("e1",)
) -> RouteSpan:
    return RouteSpan("ch", runner, "ws", envs, at(created), at(released) if released is not None else None)


def _facts(chunk: ChunkTimelineFacts, *routes: RouteSpan) -> EnvTimelineFacts:
    return EnvTimelineFacts(
        routes=routes or (_route(0),),
        chunks={chunk.chunk_id: chunk},
        roster=ROSTER,
        graphs={"gr": GRAPH, "gr2": NEXT_GRAPH},
    )


def _segments(facts: EnvTimelineFacts, *, now: float = 60) -> list[EnvSegment]:
    timeline = env_timeline(facts, since=T0, until=at(1000), now=at(now))
    return [s for h in timeline.hosts for ws in h.workspaces for env in ws.environments for s in env.segments]


def _chunk(
    *transitions: StepTransition,
    endings: tuple[datetime, ...] = (),
    escalations: tuple[StepEscalation, ...] = (),
    usage: tuple[StepUsage, ...] = (),
    leases: tuple[StepLease, ...] = (),
) -> ChunkTimelineFacts:
    return ChunkTimelineFacts(
        chunk_id="ch",
        graph_id="gr",
        endings=endings,
        transitions=transitions,
        escalations=escalations,
        usage=usage,
        leases=leases,
    )


# --- route spans and windows -------------------------------------------------------------------------------------


def test_each_route_is_released_by_the_first_release_of_its_chunk_after_it() -> None:
    created = [
        RouteCreated("ch", "rn", "ws", ("e1",), at(0), seq=1),
        RouteCreated("ch", "rn", "ws", ("e1",), at(10), 3),
    ]
    released = [RouteReleased("ch", at(20), seq=4), RouteReleased("ch", at(5), seq=2)]

    spans = route_spans(created, released)

    assert [s.released_at for s in spans] == [at(5), at(20)]


def test_a_route_with_no_release_after_it_still_holds_the_chunk() -> None:
    (span,) = route_spans([RouteCreated("ch", "rn", "ws", ("e1",), at(10), seq=3)], [RouteReleased("ch", at(5), 2)])
    assert span.released_at is None


def test_a_window_wider_than_the_cap_is_narrowed_to_the_cap_before_its_end() -> None:
    assert window_start(at(0), at(10)) == at(0)
    assert window_start(T0 - timedelta(days=30), T0) == T0 - MAX_WINDOW


# --- the entry step ----------------------------------------------------------------------------------------------


def test_a_chunk_with_no_transition_sits_on_its_graphs_entry_node_from_when_it_was_routed() -> None:
    (segment,) = _segments(_facts(_chunk()))

    assert (segment.node_id, segment.node_name, segment.started_at, segment.ended_at) == ("build", "build", T0, None)


def test_the_entry_step_runs_from_the_route_to_the_first_transition_out_of_it() -> None:
    chunk = _chunk(StepTransition(at(10), "gr", "verify", "pass", from_node_id="build"))

    segments = _segments(_facts(chunk))

    assert [(s.node_id, s.started_at, s.ended_at, s.exit_choice) for s in segments] == [
        ("build", at(0), at(10), "pass"),
        ("verify", at(10), None, None),
    ]


def test_a_first_transition_at_or_before_the_route_opened_leaves_no_entry_step() -> None:
    chunk = _chunk(StepTransition(at(0), "gr", "verify", "pass", from_node_id="build"))

    segments = _segments(_facts(chunk))

    assert [s.node_id for s in segments] == ["verify"]


def test_a_chunk_on_no_known_graph_shows_only_its_transitions() -> None:
    chunk = ChunkTimelineFacts(chunk_id="ch", graph_id="gr-missing")
    assert _segments(_facts(chunk)) == []


# --- graph crossings ---------------------------------------------------------------------------------------------


def test_a_move_onto_another_graph_closes_the_step_on_the_graph_it_left() -> None:
    chunk = _chunk(
        StepTransition(at(10), "gr2", "plan", "pass", from_node_id="build", from_graph_id="gr"),
        StepTransition(at(20), "gr2", "ship", "pass", from_node_id="plan"),
    )

    segments = _segments(_facts(chunk))

    assert [(s.graph_name, s.node_name, s.exit_choice) for s in segments] == [
        ("dwf", "build", "pass"),
        ("dwf2", "plan", "pass"),
        ("dwf2", "ship", None),
    ]
    assert [s.graph_id for s in segments] == ["gr", "gr2", "gr2"]


def test_a_move_recorded_without_its_landing_lands_on_the_target_entry_unless_a_node_there_shares_its_name() -> None:
    chunk = replace(_chunk(), migrations=(StepMigration(at(10), "gr", "gr2", "build", None, "move", epoch=2),))

    assert [(s.graph_id, s.node_id, s.exit_choice) for s in _segments(_facts(chunk))] == [
        ("gr", "build", "move"),
        ("gr2", "plan", None),
    ]
    assert step_detail(_facts(chunk), chunk_id="ch", now=at(60)).step.segment.node_name == "plan"
    named = replace(NEXT_GRAPH, nodes=[*NEXT_GRAPH.nodes, _node("gr2", "build2", "build", "pass")])
    facts = replace(_facts(chunk), graphs={"gr": GRAPH, "gr2": named})
    assert [s.node_id for s in _segments(facts)] == ["build", "build2"]


def test_a_move_without_its_landing_onto_a_graph_the_hub_does_not_know_ends_the_step_it_left_and_opens_none() -> None:
    chunk = replace(_chunk(), migrations=(StepMigration(at(10), "gr", "gr-missing", "build", None, "move"),))

    assert [(s.node_id, s.ended_at, s.exit_choice) for s in _segments(_facts(chunk))] == [("build", at(10), "move")]


def test_a_move_without_its_landing_out_of_a_node_the_hub_cannot_resolve_ends_the_step_it_left_and_opens_none() -> None:
    chunk = replace(
        _chunk(StepTransition(at(10), "gr", "verify", "pass", from_node_id="build")),
        migrations=(StepMigration(at(20), "gr", "gr2", "gone", None, "move"),),
    )

    assert [(s.node_id, s.ended_at, s.exit_choice) for s in _segments(_facts(chunk))] == [
        ("build", at(10), "pass"),
        ("verify", at(20), "move"),
    ]


# --- failure, cost, tenure ---------------------------------------------------------------------------------------


def test_a_step_left_by_a_non_first_choice_is_failed_and_the_open_step_runs_to_now() -> None:
    chunk = _chunk(
        StepTransition(at(0), "gr", "build", None),
        StepTransition(at(10), "gr", "verify", "pass"),
        StepTransition(at(20), "gr", "build", "fail"),
        usage=(StepUsage("verify", at(15), 0.5, None), StepUsage("verify", at(16), None, 0.25)),
    )

    segments = _segments(_facts(chunk), now=30)

    assert [(s.node_name, s.exit_choice, s.failed) for s in segments] == [
        ("build", "pass", False),
        ("verify", "fail", True),
        ("build", None, False),
    ]
    assert (segments[1].cost_usd, segments[1].cost_estimated) == (0.75, True)
    assert segments[-1].ended_at is None


def test_steps_are_clipped_to_the_route_and_an_escalated_step_is_failed() -> None:
    chunk = _chunk(
        StepTransition(at(0), "gr", "build", None), escalations=(StepEscalation(at(5), "retries-exhausted"),)
    )

    (segment,) = _segments(_facts(chunk, _route(0, released=8)))

    assert (segment.ended_at, segment.failed, segment.escalation) == (at(8), True, "retries-exhausted")


def test_an_unreleased_route_ends_at_the_chunks_first_ending_after_it_opened() -> None:
    chunk = _chunk(endings=(at(-30), at(12), at(40)))

    (segment,) = _segments(_facts(chunk))

    assert segment.ended_at == at(12)


def test_the_terminal_node_holds_no_step() -> None:
    chunk = _chunk(StepTransition(at(10), "gr", "done", "pass", from_node_id="build"))

    segments = _segments(_facts(chunk, _route(0, released=12)))

    assert [(s.node_id, s.ended_at) for s in segments] == [("build", at(10))]


# --- the lease a step ran ----------------------------------------------------------------------------------------


def _lease(epoch: int, minted: float) -> StepLease:
    return StepLease(epoch, f"lease_{epoch}", at(minted))


def _one(
    leases: list[StepLease],
    start: datetime,
    end: datetime | None,
    exit_epoch: int | None,
    executor: Executor | None = Executor.RUNNER,
) -> StepLease | None:
    (lease,) = step_leases([StepWindow(start, end, exit_epoch, executor)], leases)
    return lease


def test_a_step_runs_the_lease_its_exit_is_fenced_by_when_minted_inside_it() -> None:
    leases = [_lease(1, 1), _lease(2, 3)]
    assert _one(leases, at(0), at(10), exit_epoch=1) == leases[0]


def test_a_fenced_lease_minted_outside_the_step_gives_way_to_the_newest_minted_inside() -> None:
    leases = [_lease(1, 20), _lease(2, 3), _lease(3, 5)]
    assert _one(leases, at(0), at(10), exit_epoch=1) == leases[2]


def test_a_lease_minted_just_before_its_step_opened_is_still_its_own() -> None:
    lease = StepLease(1, "lease_1", at(10) - LEASE_SLACK)
    assert _one([lease], at(10), at(20), exit_epoch=1) == lease
    late = StepLease(1, "lease_1", at(10) - LEASE_SLACK - timedelta(seconds=1))
    assert _one([late], at(10), at(20), exit_epoch=1) is None


def test_a_running_step_runs_the_newest_lease_minted_since_it_opened() -> None:
    leases = [_lease(1, -10), _lease(2, 5)]
    assert _one(leases, at(0), None, exit_epoch=None) == leases[1]


def test_a_runner_step_no_lease_was_minted_inside_has_none() -> None:
    assert _one([_lease(1, -10), _lease(2, 30)], at(0), at(10), exit_epoch=2) is None
    assert _one([], at(0), None, exit_epoch=None) is None


def test_a_hub_step_runs_the_hubs_own_mint_and_a_runner_step_never_does() -> None:
    hub_mint = StepLease(5, None, at(1), hub=True)
    leases = [hub_mint, _lease(6, 2)]
    assert _one(leases, at(0), at(10), exit_epoch=5, executor=Executor.HUB) == hub_mint
    assert _one([hub_mint], at(0), at(10), exit_epoch=None) is None


def test_a_runners_forced_entry_takeover_runs_a_runner_step_though_it_names_no_lease() -> None:
    takeover = StepLease(5, None, at(1))
    assert _one([takeover], at(0), at(10), exit_epoch=5) == takeover
    assert _one([takeover], at(0), at(10), exit_epoch=5, executor=Executor.HUB) is None


def _seconds(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def test_a_hub_step_never_takes_the_lease_minted_just_before_the_runner_step_after_it() -> None:
    # The hub step [0s, 10s) is fenced out by the hub's epoch 5; the runner minted lease_B at 8s for the step it
    # opens at 10s, before the transition into that step was recorded.
    leases = [StepLease(5, None, _seconds(0), hub=True), StepLease(6, "lease_B", _seconds(8))]
    steps = [
        StepWindow(_seconds(0), _seconds(10), 5, Executor.HUB),
        StepWindow(_seconds(10), None, None, Executor.RUNNER),
    ]

    assert step_leases(steps, leases) == [leases[0], leases[1]]

    graph = Graph(
        graph_id="gr",
        name="dwf",
        entry_node_id="gate",
        nodes=[
            replace(_node("gr", "gate", "gate", "pass"), executor=Executor.HUB),
            _node("gr", "build", "build", "pass"),
        ],
        edges=[],
        created_at=T0,
    )
    chunk = _chunk(
        StepTransition(_seconds(10), "gr", "build", "pass", epoch=5, from_node_id="gate"), leases=tuple(leases)
    )
    facts = replace(_facts(chunk), graphs={"gr": graph})
    segments = _segments(facts, now=1)
    assert [(s.node_id, s.executor, s.epoch, s.lease_id) for s in segments] == [
        ("gate", Executor.HUB, 5, None),
        ("build", Executor.RUNNER, 6, "lease_B"),
    ]
    detail = step_detail(facts, chunk_id="ch", lease_id="lease_B", now=_seconds(60))
    assert detail.step.segment.node_id == "build"


def test_a_lease_minted_just_before_a_step_opened_is_never_the_step_before_its_fallback() -> None:
    # The runner step [0s, 10s) exits under an epoch no mint names; lease_B, minted at 8s, opens the step after.
    leases = [StepLease(6, "lease_B", _seconds(8))]
    steps = [
        StepWindow(_seconds(0), _seconds(10), 4, Executor.RUNNER),
        StepWindow(_seconds(10), _seconds(30), 6, Executor.RUNNER),
    ]

    assert step_leases(steps, leases) == [None, leases[0]]


def test_no_lease_runs_two_steps() -> None:
    lease = StepLease(1, "lease_1", _seconds(8))
    steps = [
        StepWindow(_seconds(0), _seconds(10), 1, Executor.RUNNER),
        StepWindow(_seconds(10), None, None, Executor.RUNNER),
    ]

    assert step_leases(steps, [lease]) == [lease, None]


def test_a_leased_steps_cost_is_its_epochs_invocations_at_its_node() -> None:
    chunk = _chunk(
        StepTransition(at(10), "gr", "verify", "pass", epoch=1, from_node_id="build"),
        leases=(_lease(1, 1),),
        usage=(
            StepUsage("build", at(2), 1.0, None, epoch=1),
            StepUsage("build", at(3), 2.0, None, epoch=9),
            StepUsage("verify", at(4), 4.0, None, epoch=1),
        ),
    )

    build = _segments(_facts(chunk))[0]

    assert (build.epoch, build.lease_id, build.cost_usd, build.cost_estimated) == (1, "lease_1", 1.0, False)


# --- across environments -----------------------------------------------------------------------------------------


def test_a_chunk_rerouted_mid_step_shows_the_step_on_each_environment_that_held_it() -> None:
    chunk = _chunk(StepTransition(at(20), "gr", "verify", "pass", from_node_id="build"))
    facts = _facts(chunk, _route(0, released=8, envs=("e1",)), _route(10, runner="rn2", envs=("e2", "e3")))

    timeline = env_timeline(facts, since=T0, until=at(1000), now=at(30))

    lanes = {env.environment_id: env.segments for ws in timeline.hosts[0].workspaces for env in ws.environments}
    assert [(s.node_id, s.started_at, s.ended_at) for s in lanes["e1"]] == [("build", at(0), at(8))]
    assert lanes["e2"] == lanes["e3"]
    assert [(s.node_id, s.runner_name, s.started_at) for s in lanes["e2"]] == [
        ("build", "r-codex", at(10)),
        ("verify", "r-codex", at(20)),
    ]
    assert timeline.hosts[0].runner_names == ("r-claude", "r-codex")


def test_only_steps_touching_the_window_are_laid_on_the_environments() -> None:
    chunk = _chunk(StepTransition(at(10), "gr", "verify", "pass", from_node_id="build"))

    timeline = env_timeline(_facts(chunk), since=at(15), until=at(1000), now=at(30))

    assert [s.node_id for s in timeline.hosts[0].workspaces[0].environments[0].segments] == ["verify"]


def test_a_runner_on_no_host_record_groups_under_no_host() -> None:
    facts = _facts(_chunk(), _route(0, runner="unregistered"))

    (host,) = env_timeline(facts, since=T0, until=at(100), now=at(10)).hosts

    assert (host.host_id, host.host_name, host.runner_names) == (None, None, ())
    assert host.workspaces[0].environments[0].segments[0].runner_name == "unregistered"


def test_the_newest_route_per_environment_and_the_newest_step_per_lane() -> None:
    old, new = _route(0, released=5), _route(10)
    assert newest_per_environment([new, old], ROSTER) == {("hs", "ws", "e1"): new}

    chunk = _chunk(StepTransition(at(10), "gr", "verify", "pass", from_node_id="build"))
    latest = latest_only(env_timeline(_facts(chunk), since=T0, until=at(100), now=at(30)))
    assert [s.node_id for s in latest.hosts[0].workspaces[0].environments[0].segments] == ["verify"]


# --- one step in depth -------------------------------------------------------------------------------------------


def _detail_facts() -> EnvTimelineFacts:
    chunk = _chunk(
        StepTransition(at(10), "gr", "verify", "pass", epoch=1, from_node_id="build"),
        leases=(_lease(1, 1),),
        usage=(StepUsage("build", at(3), 1.0, None, epoch=1), StepUsage("build", at(2), 0.5, None, epoch=1)),
        escalations=(StepEscalation(at(4), "stuck", "detail", epoch=1), StepEscalation(at(12), "later")),
    )
    return _facts(chunk, _route(0, released=8, envs=("e1",)), _route(9, envs=("e2",)))


def _starts(journey: tuple[PlacedSegment, ...]) -> list[datetime]:
    return [p.segment.started_at for p in journey]


def test_a_step_is_found_by_the_lease_that_ran_it_with_its_invocations_and_escalations() -> None:
    detail = step_detail(_detail_facts(), chunk_id="ch", lease_id="lease_1", now=at(20))

    assert (detail.step.environment_id, detail.step.host_name, detail.step.segment.node_id) == ("e1", "xps", "build")
    assert [u.recorded_at for u in detail.invocations] == [at(2), at(3)]
    assert [e.cause for e in detail.escalations] == ["stuck"]
    assert _starts(detail.journey) == [at(0), at(9), at(10)]
    assert detail.generated_at == at(20)


def test_a_step_no_lease_ran_is_found_by_its_start_to_the_millisecond() -> None:
    facts = _detail_facts()

    detail = step_detail(facts, chunk_id="ch", started_at=at(9) + timedelta(microseconds=400), now=at(20))

    assert (detail.step.environment_id, detail.step.segment.started_at) == ("e2", at(9))
    with pytest.raises(StepNotFound):
        step_detail(facts, chunk_id="ch", started_at=at(9) + timedelta(milliseconds=2), now=at(20))


def test_with_neither_lease_nor_start_the_chunks_newest_step_is_answered() -> None:
    detail = step_detail(_detail_facts(), chunk_id="ch", now=at(20))

    assert (detail.step.segment.node_id, detail.step.segment.ended_at) == ("verify", None)
    assert [e.cause for e in detail.escalations] == ["later"]


def test_an_unknown_chunk_lease_or_a_chunk_never_routed_has_no_step() -> None:
    facts = _detail_facts()
    with pytest.raises(StepNotFound):
        step_detail(facts, chunk_id="ch_other", now=at(20))
    with pytest.raises(StepNotFound):
        step_detail(facts, chunk_id="ch", lease_id="lease_missing", now=at(20))
    unrouted = EnvTimelineFacts(routes=(), chunks=facts.chunks, roster=ROSTER, graphs=facts.graphs)
    with pytest.raises(StepNotFound):
        step_detail(unrouted, chunk_id="ch", now=at(20))


# --- escalations, spend and artifacts ------------------------------------------------------------------------------


def _escalated(*, superseded_by: str | None = None) -> ChunkTimelineFacts:
    chunk = _chunk(
        StepTransition(at(0), "gr", "build", None),
        leases=(_lease(1, 1),),
        escalations=(StepEscalation(at(5), "retries-exhausted", epoch=1),),
    )
    if superseded_by == "requeue":
        return replace(chunk, requeues=(at(7),))
    if superseded_by == "restart":
        return replace(chunk, restarts=(at(7),))
    if superseded_by == "end":
        return replace(chunk, endings=(at(7),))
    return chunk


def test_an_escalation_nothing_superseded_is_open_on_its_step() -> None:
    (segment,) = _segments(_facts(_escalated()))

    assert (segment.failed, segment.escalation, segment.escalation_open) == (True, "retries-exhausted", True)


@pytest.mark.parametrize("superseded_by", ["requeue", "restart", "end"])
def test_an_escalation_a_later_requeue_restart_or_ending_superseded_is_closed(superseded_by: str) -> None:
    segments = _segments(_facts(_escalated(superseded_by=superseded_by)))

    assert [(s.failed, s.escalation, s.escalation_open) for s in segments] == [(True, "retries-exhausted", False)]


def test_a_later_mint_supersedes_an_escalation_and_the_step_it_opens_does_not_carry_it() -> None:
    chunk = replace(_escalated(), leases=(_lease(1, 1), _lease(2, 8)))

    (segment,) = _segments(_facts(chunk))

    assert (segment.epoch, segment.escalation, segment.escalation_open) == (2, None, False)
    assert chunk.open_escalation() is None


def test_an_escalation_belongs_to_the_step_its_epoch_fenced_and_the_detail_agrees() -> None:
    chunk = _chunk(
        StepTransition(at(10), "gr", "verify", "pass", epoch=1, from_node_id="build"),
        leases=(_lease(1, 1), _lease(2, 10)),
        escalations=(StepEscalation(at(10), "stuck", epoch=2),),
    )
    facts = _facts(chunk)

    build, verify = _segments(facts, now=20)

    assert (build.escalation, verify.escalation, verify.escalation_open) == (None, "stuck", True)
    assert [e.cause for e in step_detail(facts, chunk_id="ch", lease_id="lease_1", now=at(20)).escalations] == []
    assert [e.cause for e in step_detail(facts, chunk_id="ch", lease_id="lease_2", now=at(20)).escalations] == ["stuck"]


def test_a_step_with_no_fence_holds_the_escalations_recorded_before_its_exit_not_at_it() -> None:
    chunk = _chunk(
        StepTransition(at(10), "gr", "verify", "pass", from_node_id="build"),
        escalations=(StepEscalation(at(10), "stuck"),),
    )
    facts = _facts(chunk)

    build, verify = _segments(facts, now=20)

    assert (build.escalation, verify.escalation) == (None, "stuck")
    assert step_detail(facts, chunk_id="ch", started_at=at(0), now=at(20)).escalations == ()


def test_a_steps_spend_sums_its_invocations_under_the_chunk_totals_contract() -> None:
    chunk = _chunk(
        StepTransition(at(10), "gr", "verify", "pass", epoch=1, from_node_id="build"),
        leases=(_lease(1, 1),),
        usage=(
            StepUsage("build", at(2), 1.0, None, epoch=1, input_tokens=10),
            StepUsage("build", at(3), None, 0.5, epoch=1, output_tokens=4),
            StepUsage("build", at(4), None, None, epoch=1),
        ),
    )
    facts = _facts(chunk)

    detail = step_detail(facts, chunk_id="ch", lease_id="lease_1", now=at(20))

    spend = detail.spend
    assert spend is not None
    assert (spend.cost_usd, spend.estimated_cost_usd, spend.cost_partial, spend.billed_partial) == (
        1.0,
        0.5,
        True,
        True,
    )
    assert (spend.input_tokens, spend.output_tokens) == (10, 4)
    assert (detail.step.segment.cost_usd, detail.step.segment.cost_estimated) == (1.5, True)
    assert step_detail(facts, chunk_id="ch", started_at=at(10), now=at(20)).spend is None


def test_a_steps_artifacts_are_its_nodes_under_its_fence_else_inside_its_span() -> None:
    chunk = _chunk(
        StepTransition(at(10), "gr", "verify", "pass", epoch=1, from_node_id="build"),
        leases=(_lease(1, 1),),
    )
    artifacts = [
        StepArtifact("build.plan.1", "build", 1, at(3)),
        StepArtifact("build.plan.0", "build", 0, at(4)),
        StepArtifact("verify.plan.1", "verify", 1, at(5)),
        StepArtifact("verify.report.7", "verify", 7, at(12)),
        StepArtifact("verify.report.8", "verify", 8, at(8)),
        StepArtifact("verify.undated.7", "verify", 7, None),
    ]
    facts = _facts(chunk)

    leased = step_detail(facts, chunk_id="ch", lease_id="lease_1", artifacts=artifacts, now=at(20))
    unfenced = step_detail(facts, chunk_id="ch", started_at=at(10), artifacts=artifacts, now=at(20))

    assert leased.artifact_keys == ("build.plan.1",)
    assert unfenced.artifact_keys == ("verify.report.7",)
