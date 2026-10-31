"""Parked changes are watched without the hub deciding their merge."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.hub.delivery.actions.land import Land
from blizzard.hub.domain.config.landing_policies import LandingPolicy
from blizzard.hub.domain.config.repositories import RepositoryId
from blizzard.hub.forge.landing import ChangeState
from tests.github_double import change
from tests.test_land_action import _GADGET, _SUBMITTED, _WIDGET, _forge, _land, _row, _Sink, _visit

pytestmark = pytest.mark.component


def parked_visit(repos=(_WIDGET,)):
    visit = _visit(repos)
    targets = tuple(
        replace(t, landing_policy=LandingPolicy.PARK) if i == 0 else t for i, t in enumerate(visit.resolution.targets)
    )
    return replace(visit, resolution=replace(visit.resolution, targets=targets))


def marker(visit, epoch=1, age=0):
    return replace(
        _row(ArtifactKind.ASSET, f"delivery-pr/{_WIDGET}/1", "{}"),
        epoch=epoch,
        produced_at=visit.now - timedelta(hours=age),
    )


def test_open_parked_change_never_runs_checks_or_merge():
    forge = _forge()
    answer, sink = _land(forge, parked_visit())
    assert answer.choice == "awaiting-merge"
    assert f"delivery-pr/{_WIDGET}/1" in sink.markers()
    assert "awaiting-external-merge" in sink.markers()
    assert forge.writes() == ["open_change"]
    assert not forge.named("checks")


def test_timeout_and_new_epoch_adoption():
    visit = parked_visit()
    forge = _forge()
    forge.repo(_WIDGET).changes[1] = change(_WIDGET, 1, branch="feat/x", sha=_SUBMITTED, base="main")
    answer, _ = _land(forge, replace(visit, markers=(marker(visit, age=168),)))
    assert answer.escalation and answer.escalation.cause == EscalationCause.PARK_TIMEOUT
    answer, sink = _land(forge, replace(visit, epoch=2, markers=(marker(visit, age=168),)))
    assert answer.choice == "awaiting-merge"
    assert f"delivery-pr/{_WIDGET}/1" in sink.markers()
    assert "awaiting-external-merge" in sink.markers()


def test_open_parked_change_excludes_later_repositories():
    visit = parked_visit((_WIDGET, _GADGET))
    forge = _forge(_WIDGET, _GADGET)
    answer, _ = _land(forge, replace(visit, markers=(marker(visit),)))
    assert answer.choice == "awaiting-merge"
    assert all(repo == _WIDGET for _, repo, _ in forge.calls)


def test_partial_forge_set_watches_only_parked_repo_then_defers_after_merge():
    visit = parked_visit((_WIDGET, _GADGET))
    visit = replace(visit, markers=(marker(visit),))
    forge = _forge(_WIDGET, _GADGET)
    repository = RepositoryId(visit.resolution.targets[0].record)
    assert visit.parked_watch() == repository
    handles = {repository: forge.forge(repository, _WIDGET)}
    answer = asyncio.run(Land().take(visit, handles, _Sink()))
    assert answer.choice == "awaiting-merge"
    forge.repo(_WIDGET).changes[1] = change(
        _WIDGET, 1, branch="feat/x", sha=_SUBMITTED, base="main", state=ChangeState.MERGED, merged_revision="merged"
    )
    sink = _Sink()
    answer = asyncio.run(Land().take(visit, handles, sink))
    assert answer.choice == "pending"
    assert sink.landings()[0].revision == "merged"
    assert all(repo == _WIDGET for _, repo, _ in forge.calls)


def test_merged_parked_change_records_actual_revision():
    visit = parked_visit()
    forge = _forge()
    forge.repo(_WIDGET).changes[1] = change(
        _WIDGET, 1, branch="feat/x", sha=_SUBMITTED, base="main", state=ChangeState.MERGED, merged_revision="merged"
    )
    answer, sink = _land(forge, replace(visit, markers=(marker(visit),)))
    assert answer.choice == "landed"
    assert sink.landings()[0].revision == "merged"


def test_merge_seen_after_open_before_marker_recovers_both_park_markers():
    visit = parked_visit()
    forge = _forge()
    forge.repo(_WIDGET).changes[1] = replace(
        change(
            _WIDGET, 1, branch="feat/x", sha=_SUBMITTED, base="main", state=ChangeState.MERGED, merged_revision="merged"
        ),
        closed_at=visit.now,
    )
    answer, sink = _land(forge, visit)
    assert answer.choice == "landed"
    assert sink.landings()[0].revision == "merged"
    assert f"delivery-pr/{_WIDGET}/1" in sink.markers()
    assert "awaiting-external-merge" in sink.markers()


def test_closed_change_escalates_and_old_closed_change_is_replaced():
    visit = parked_visit()
    forge = _forge()
    closed = change(_WIDGET, 1, branch="feat/x", sha=_SUBMITTED, base="main", state=ChangeState.CLOSED)
    forge.repo(_WIDGET).changes[1] = replace(closed, closed_at=visit.now)
    answer, _ = _land(forge, replace(visit, markers=(marker(visit),)))
    assert answer.escalation and answer.escalation.cause == EscalationCause.CHANGE_CLOSED
    forge.repo(_WIDGET).changes[1] = replace(closed, closed_at=visit.first_turn_at - timedelta(seconds=1))
    answer, sink = _land(forge, replace(visit, epoch=2, markers=(marker(visit),)))
    assert answer.choice == "pending"
    assert f"park-reopen/{_WIDGET}/1" in sink.markers()
    assert "open_change" not in forge.writes()


def test_requeued_closed_change_waits_until_other_heads_are_reproved():
    visit = parked_visit((_WIDGET, _GADGET))
    forge = _forge(_WIDGET, _GADGET)
    closed = change(_WIDGET, 1, branch="feat/x", sha=_SUBMITTED, base="main", state=ChangeState.CLOSED)
    forge.repo(_WIDGET).changes[1] = replace(closed, closed_at=visit.first_turn_at - timedelta(seconds=1))
    requeued = replace(visit, epoch=2, markers=(marker(visit),))
    answer, _ = _land(forge, requeued)
    assert answer.choice == "pending"
    reopened = replace(_row(ArtifactKind.ASSET, f"park-reopen/{_WIDGET}/1", ""), epoch=2)
    forge.repo(_GADGET).checks[_SUBMITTED] = ()
    answer, _ = _land(forge, replace(requeued, markers=(*requeued.markers, reopened)))
    assert answer.choice == "pending"
    assert not forge.named("open_change") or all(repo == _GADGET for repo, _ in forge.named("open_change"))


def test_unopened_park_waits_for_other_repositories_checks():
    visit = parked_visit((_WIDGET, _GADGET))
    forge = _forge(_WIDGET, _GADGET)
    forge.repo(_GADGET).checks[_SUBMITTED] = ()
    answer, _ = _land(forge, visit)
    assert answer.choice == "pending"
    assert not any(repo == _WIDGET for _, repo, _ in forge.calls)
