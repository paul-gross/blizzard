"""``combine`` folds a pass's repository states into one answer over stub states: a person's call, then a foreign
head, a conflict, owned failures, inherited failures, and waits, in that priority; nothing lands while any repository
is unprepared; and once every one is prepared, each lands in its project's landing order, a refusal after a landing
leaving the landed repository standing."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, cast

import pytest

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.hub.delivery.actions.landing.combine import combine, in_landing_order
from blizzard.hub.delivery.actions.landing.repo import (
    AwaitingMerge,
    Conflict,
    Failure,
    Inherited,
    Landed,
    LandingRun,
    NeedsPerson,
    Ready,
    RepoLanding,
    RepoState,
    Waiting,
)
from blizzard.hub.domain.actions.effects import (
    ActionEscalation,
    Effect,
    EffectResult,
    EffectStatus,
    LandingRecord,
    Marker,
    Pass,
)
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.config.repositories import ResolvedRepository
from blizzard.hub.forge.landing import RepositoryForge

pytestmark = pytest.mark.component


class _Sink:
    def __init__(self) -> None:
        self.effects: list[Effect] = []

    async def record(self, effect: Effect) -> EffectResult:
        self.effects.append(effect)
        return EffectResult(EffectStatus.RECORDED)

    def landed(self) -> list[str]:
        return [e.repo for e in self.effects if isinstance(e, LandingRecord)]

    def markers(self) -> dict[str, str]:
        return {e.name: e.content for e in self.effects if isinstance(e, Marker)}


def _run() -> tuple[LandingRun, _Sink]:
    sink = _Sink()
    return LandingRun(cast(Visit, None), sink), sink


def _repo(run: LandingRun, slug: str, position: float | None = None) -> RepoLanding:
    record = ResolvedRepository(
        ForgeId("frg_1"), "acme", "main", "s", record=f"re_{slug}", slug=slug, landing_position=position
    )
    return RepoLanding(run, f"acme/{slug}", "feat/x", "sha", record, cast(RepositoryForge, None), "forge")


def _record(slug: str, decision: str) -> dict[str, Any]:
    return {"repo": f"acme/{slug}", "number": 1, "url": "u", "decision": decision, "checks": [], "base_branch": "main"}


def _foreign(slug: str) -> Failure:
    record = {"repo": f"acme/{slug}", "number": 1, "url": "u", "decision": "foreign-head"}
    return Failure(({**record, "submitted": "s", "head": "h", "offenders": ["h"]},), foreign_head=True)


def _ready(revision: str, landings: list[str] | None = None) -> Ready:
    async def land() -> Landed | Waiting:
        if landings is not None:
            landings.append(revision)
        return Landed(revision)

    return Ready(land)


def _refused(reason: str) -> Ready:
    async def land() -> Landed | Waiting:
        return Waiting(reason)

    return Ready(land)


def _fold(states: Sequence[tuple[str, RepoState]]) -> tuple[Pass, _Sink]:
    run, sink = _run()
    return asyncio.run(combine(run, [(_repo(run, slug), state) for slug, state in states])), sink


_ESCALATION = ActionEscalation(EscalationCause.FORGE_AUTH, "acme/a: rejected")

# One repository per verdict, from the strongest down; each answer outranks every one after it.
_PRIORITY: list[tuple[RepoState, str | None]] = [
    (NeedsPerson(_ESCALATION), None),
    (_foreign("b"), "failure"),
    (Conflict("dirty"), "conflict"),
    (Failure((_record("d", "failed"),)), "failure"),
    (Inherited((_record("e", "inherited-failure"),)), "inherited-failure"),
    (Waiting("running"), "pending"),
]


@pytest.mark.parametrize("strongest", range(len(_PRIORITY)))
def test_the_strongest_verdict_present_answers_the_pass_and_nothing_lands(strongest: int) -> None:
    present = _PRIORITY[strongest:]
    states = [(f"r{index}", state) for index, (state, _) in enumerate(present)] + [("z", _ready("rz"))]

    answer, sink = _fold(states)

    expected = present[0][1]
    assert answer.choice == expected
    assert (answer.escalation is not None) == (expected is None)
    assert sink.landed() == []


def test_a_foreign_head_writes_both_findings_markers_and_outranks_a_conflict_elsewhere() -> None:
    answer, sink = _fold([("a", Conflict("dirty")), ("b", _foreign("b"))])

    assert answer.choice == "failure"
    assert set(sink.markers()) == {"delivery-findings", "delivery-findings/foreign-head"}


def test_owned_failures_across_repositories_are_gathered_into_one_findings_marker() -> None:
    answer, sink = _fold([("a", Failure((_record("a", "failed"),))), ("b", Failure((_record("b", "failed"),)))])

    assert answer.choice == "failure"
    assert "acme/a#1" in sink.markers()["delivery-findings"] and "acme/b#1" in sink.markers()["delivery-findings"]


def test_pending_while_any_repository_is_unprepared_and_none_lands() -> None:
    landings: list[str] = []

    answer, sink = _fold([("a", _ready("ra", landings)), ("b", Waiting("checks running")), ("c", Landed("rc"))])

    assert answer.choice == "pending"
    assert landings == []
    assert sink.landed() == ["acme/c"]  # a repository already landed is handed on whatever the pass answers


def test_every_prepared_repository_lands_in_landing_order() -> None:
    run, sink = _run()
    unranked_b, unranked_a = _repo(run, "b"), _repo(run, "a")
    second, first = _repo(run, "z", 2.0), _repo(run, "y", 1.0)
    stepped: list[tuple[RepoLanding, RepoState]] = [
        (unranked_b, _ready("rb")),
        (second, _ready("rz")),
        (unranked_a, _ready("ra")),
        (first, _ready("ry")),
    ]

    answer = asyncio.run(combine(run, stepped))

    assert answer.choice == "landed"
    assert sink.landed() == ["acme/y", "acme/z", "acme/a", "acme/b"]
    assert [r.repo for r in in_landing_order([unranked_b, second, unranked_a, first])] == sink.landed()


def test_a_refusal_after_a_landing_answers_pending_and_leaves_the_landed_repository_standing() -> None:
    run, sink = _run()
    stepped: list[tuple[RepoLanding, RepoState]] = [
        (_repo(run, "lib", 1.0), _ready("rlib")),
        (_repo(run, "app", 2.0), _refused("merge of acme/app#1 did not land")),
    ]

    answer = asyncio.run(combine(run, stepped))

    assert answer.choice == "pending"
    assert sink.landed() == ["acme/lib"]
    assert "merge of acme/app#1 did not land" in answer.log


def test_an_open_park_waits_without_landing_a_prepared_repository() -> None:
    answer, sink = _fold([("a", AwaitingMerge(7)), ("b", _ready("rb"))])
    assert answer.choice == "awaiting-merge"
    assert sink.landed() == []
