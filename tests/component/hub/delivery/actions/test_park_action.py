"""Parked changes are watched without the hub deciding their merge."""

import asyncio
import json
from dataclasses import replace
from datetime import timedelta

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.landing_policy import LandingPolicy
from blizzard.hub.delivery.actions.land import Land
from blizzard.hub.delivery.actions.landing.park import Park
from blizzard.hub.delivery.actions.landing.repo import Admitted, Landed, LandingRun, RepoLanding, Waiting
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.no_code import no_code_marker
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.repositories import RepositoryId
from blizzard.hub.forge.landing import ChangeState, CommitSummary, ComparisonStatus, ForgeRejected, ForgeUnavailable
from tests.support.github_double import GitHubDouble, change, comparison
from tests.support.land_action_doubles import (
    _GADGET,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.land_action_doubles
    _SUBMITTED,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.land_action_doubles
    _WIDGET,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.land_action_doubles
    _forge,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.land_action_doubles
    _land,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.land_action_doubles
    _row,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.land_action_doubles
    _Sink,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.land_action_doubles
    _visit,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.land_action_doubles
)


def parked_visit(repos: tuple[str, ...] = (_WIDGET,), *, park_index: int = 0) -> Visit:
    visit = _visit(repos)
    targets = tuple(
        replace(t, landing_policy=LandingPolicy.PARK) if i == park_index else t
        for i, t in enumerate(visit.resolution.targets)
    )
    return replace(visit, resolution=replace(visit.resolution, targets=targets))


def marker(visit: Visit, epoch: int = 1, age: int = 0) -> StoredArtifact:
    return replace(
        _row(ArtifactKind.ASSET, f"delivery-pr/{_WIDGET}/1", "{}"),
        epoch=epoch,
        produced_at=visit.now - timedelta(hours=age),
    )


def _assess(forge: GitHubDouble, visit: Visit):
    sink = _Sink()
    repository = RepositoryId(visit.resolution.targets[0].record)
    run = LandingRun(visit, sink)
    record = visit.resolution.targets[0]
    repo = RepoLanding(
        run,
        _WIDGET,
        "feat/x",
        _SUBMITTED,
        record,
        forge.forge(repository, _WIDGET),
        visit.resolution.forge_of(record).label,
    )
    return asyncio.run(Park().step(repo)), sink


def test_initial_park_assessment_is_read_only_and_admits_unmerged_work():
    visit = parked_visit()
    forge = _forge()

    state, sink = _assess(forge, visit)

    assert isinstance(state, Admitted)
    assert forge.named("changes")
    assert forge.writes() == []
    assert sink.effects == []
    assert not forge.named("compare") and not forge.named("branch_tip")


def test_initial_park_assessment_recovers_in_visit_merge_before_admission():
    visit = parked_visit()
    forge = _forge()
    forge.repo(_WIDGET).changes[1] = _merged(visit, head="person-added")

    state, sink = _assess(forge, visit)

    assert isinstance(state, Landed)
    assert state.revision == "merged"
    assert sink.effects == []
    assert forge.writes() == []
    assert not forge.named("compare") and not forge.named("branch_tip")


def test_initial_park_assessment_does_not_recover_a_previsit_merge():
    visit = parked_visit()
    forge = _forge()
    forge.repo(_WIDGET).changes[1] = replace(_merged(visit), closed_at=visit.first_turn_at - timedelta(seconds=1))

    state, sink = _assess(forge, visit)

    assert isinstance(state, Admitted)
    assert sink.effects == [] and forge.writes() == []


@pytest.mark.parametrize("field", ["head", "base"])
def test_initial_park_assessment_does_not_recover_an_unassociated_merge(field: str):
    visit = parked_visit()
    forge = _forge()
    forge.repo(_WIDGET).changes[1] = replace(_merged(visit), **{field: "other"})

    state, sink = _assess(forge, visit)

    assert isinstance(state, Admitted)
    assert sink.effects == [] and forge.writes() == []


def test_initial_park_assessment_waits_for_merged_revision():
    visit = parked_visit()
    forge = _forge()
    forge.repo(_WIDGET).changes[1] = _merged(visit, revision=None)

    state, sink = _assess(forge, visit)

    assert isinstance(state, Waiting)
    assert sink.effects == [] and forge.writes() == []


def test_initial_park_assessment_waits_on_refused_recovery_read():
    forge = _forge(replies={"changes": [ForgeRejected("cannot list changes")]})

    state, sink = _assess(forge, parked_visit())

    assert isinstance(state, Waiting)
    assert sink.effects == [] and forge.writes() == []


def test_initial_park_assessment_propagates_unavailable_recovery_read():
    forge = _forge(replies={"changes": [ForgeUnavailable("offline")]})
    with pytest.raises(ForgeUnavailable):
        _assess(forge, parked_visit())
    assert forge.writes() == []


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


def test_each_parked_repository_uses_its_own_timeout_and_marker_clock():
    visit = parked_visit((_WIDGET, _GADGET))
    targets = (
        replace(visit.resolution.targets[0], park_timeout_hours=1),
        replace(visit.resolution.targets[1], landing_policy=LandingPolicy.PARK, park_timeout_hours=3),
    )
    visit = replace(visit, resolution=replace(visit.resolution, targets=targets))
    old = marker(visit, age=2)
    recent = replace(
        _row(ArtifactKind.ASSET, f"delivery-pr/{_GADGET}/2", "{}"),
        produced_at=visit.now - timedelta(hours=2),
    )
    forge = _forge(_WIDGET, _GADGET)
    forge.repo(_WIDGET).changes[1] = change(_WIDGET, 1, branch="feat/x", sha=_SUBMITTED, base="main")
    forge.repo(_GADGET).changes[2] = change(_GADGET, 2, branch="feat/x", sha=_SUBMITTED, base="main")
    answer, _ = _land(forge, replace(visit, markers=(old, recent)))
    assert answer.escalation and answer.escalation.cause == EscalationCause.PARK_TIMEOUT
    assert "acme/widget#1" in answer.escalation.detail

    newer = replace(old, produced_at=visit.now - timedelta(minutes=30))
    answer, _ = _land(forge, replace(visit, markers=(newer, recent)))
    assert answer.choice == "awaiting-merge"


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
    assert visit.watched_repo() == repository
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


def test_recovery_of_merged_change_precedes_optional_no_code_proof():
    visit = parked_visit()
    forge = _forge()
    forge.repo(_WIDGET).changes[1] = _merged(visit)
    forge.repo(_WIDGET).replies["compare"] = [ForgeUnavailable("proof unavailable")]

    answer, sink = _land(forge, visit)

    assert answer.choice == "landed"
    assert [row.revision for row in sink.landings()] == ["merged"]
    assert not forge.named("compare")


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
    assert all(repo == _WIDGET for _, repo, _ in forge.calls)
    reopened = replace(_row(ArtifactKind.ASSET, f"park-reopen/{_WIDGET}/1", ""), epoch=2)
    forge.repo(_GADGET).checks[_SUBMITTED] = ()
    answer, _ = _land(forge, replace(requeued, markers=(*requeued.markers, reopened)))
    assert answer.choice == "pending"
    assert not forge.named("open_change") or all(repo == _GADGET for repo, _ in forge.named("open_change"))


def test_unopened_park_waits_for_other_repositories_checks():
    visit = parked_visit((_WIDGET, _GADGET))
    forge = _forge(_WIDGET, _GADGET)
    forge.repo(_GADGET).checks[_SUBMITTED] = ()
    answer, sink = _land(forge, visit)
    assert answer.choice == "pending"
    assert not any(repo == _WIDGET for repo, _ in forge.named("open_change"))
    assert f"delivery-pr/{_WIDGET}/1" not in sink.markers()
    assert "awaiting-external-merge" not in sink.markers()


def test_proven_no_code_park_emits_no_effect_until_sibling_is_ready():
    visit = parked_visit((_WIDGET, _GADGET))
    forge = _contained(_forge(_WIDGET, _GADGET))
    forge.repo(_GADGET).checks[_SUBMITTED] = ()

    answer, sink = _land(forge, visit)

    assert answer.choice == "pending"
    assert not any(name.startswith("no-code/") for name in sink.markers())
    assert not any(repo == _WIDGET for repo, _ in forge.named("open_change"))


def test_foreign_sibling_head_prevents_park_open_and_markers():
    visit = parked_visit((_WIDGET, _GADGET))
    forge = _forge(_WIDGET, _GADGET)
    forge.repo(_GADGET).heads["feat/x"] = "foreign"
    forge.repo(_GADGET).compares[(_SUBMITTED, "foreign")] = comparison(
        ComparisonStatus.AHEAD, commits=(CommitSummary("foreign", (_SUBMITTED,)),)
    )

    answer, sink = _land(forge, visit)

    assert answer.choice == "failure"
    assert all(repo != _WIDGET for repo, _ in forge.named("open_change"))
    assert not sink.landings()
    assert not any(name.startswith(f"delivery-pr/{_WIDGET}/") for name in sink.markers())
    assert "awaiting-external-merge" not in sink.markers()
    assert not any(repo == _WIDGET for repo, _ in forge.named("compare"))
    assert not any(repo == _WIDGET for repo, _ in forge.named("branch_tip"))


def test_parked_library_blocks_prepared_consumer_until_the_person_merges():
    visit = parked_visit((_WIDGET, _GADGET))
    visit = replace(
        visit,
        resolution=replace(
            visit.resolution,
            targets=tuple(
                replace(target, landing_position=float(index + 1))
                for index, target in enumerate(visit.resolution.targets)
            ),
        ),
    )
    forge = _forge(_WIDGET, _GADGET)
    answer, sink = _land(forge, visit)
    assert answer.choice == "awaiting-merge"
    assert sink.landings() == []
    assert forge.named("merge") == []
    assert [repo for repo, _ in forge.named("open_change")] == [_GADGET, _WIDGET]

    merged = change(
        _WIDGET, 1, branch="feat/x", sha=_SUBMITTED, base="main", state=ChangeState.MERGED, merged_revision="person"
    )
    forge.repo(_WIDGET).changes[1] = replace(merged, closed_at=visit.now)
    current = replace(visit, markers=(marker(visit),))
    answer, sink = _land(forge, current)
    assert answer.choice == "landed"
    assert [row.repo for row in sink.landings()] == [_WIDGET, _GADGET]
    assert sink.landings()[0].revision == "person"


def test_parked_consumer_opens_only_after_library_landing_is_durable():
    visit = parked_visit((_GADGET, _WIDGET), park_index=1)
    forge = _forge(_GADGET, _WIDGET)
    answer, sink = _land(forge, visit)
    assert answer.choice == "awaiting-merge"
    assert [row.repo for row in sink.landings()] == [_GADGET]
    assert [repo for repo, _ in forge.named("merge")] == [_GADGET]
    assert [repo for repo, _ in forge.named("open_change")] == [_GADGET, _WIDGET]

    held = _Sink(fence_landings=True)
    blocked, _ = _land(_forge(_GADGET, _WIDGET), visit, held)
    assert blocked.choice == "failure"
    assert f"delivery-pr/{_WIDGET}/1" not in held.markers()


_THIRD = "acme/third"


def _no_code_row(visit: Visit, repo: str) -> StoredArtifact:
    declaration = next(
        row for row in visit.git_commits.payload(visit.resolution.qualified, visit.resolution) if row["repo"] == repo
    )
    name, data = no_code_marker(declaration)
    return _row(ArtifactKind.ASSET, name, data)


def _a_b_c() -> Visit:
    """Landing order A (`widget`), B (`gadget`, PARK), C (`third`, automatic)."""
    visit = _visit((_WIDGET, _GADGET, _THIRD))
    targets = tuple(
        replace(t, landing_policy=LandingPolicy.PARK, landing_position=i) if i == 1 else replace(t, landing_position=i)
        for i, t in enumerate(visit.resolution.targets)
    )
    visit = replace(visit, resolution=replace(visit.resolution, targets=targets))
    b_pr = _row(ArtifactKind.ASSET, f"delivery-pr/{_GADGET}/2", "{}")
    return replace(visit, markers=(b_pr,), artifacts=(*visit.artifacts, b_pr))


def _with_no_code(visit: Visit, repo: str) -> Visit:
    row = _no_code_row(visit, repo)
    return replace(visit, markers=(*visit.markers, row), artifacts=(*visit.artifacts, row))


def test_the_watch_skips_a_no_code_first_repository_and_selects_the_parked_one():
    visit = _with_no_code(_a_b_c(), _WIDGET)
    assert visit.watched_repo() == RepositoryId(visit.resolution.targets[1].record)


def test_the_watch_is_none_without_no_code_evidence_for_the_earlier_repository():
    assert _a_b_c().watched_repo() is None


def test_the_watch_is_none_when_the_no_code_marker_is_from_an_earlier_node():
    visit = _a_b_c()
    row = replace(_no_code_row(visit, _WIDGET), node_id="nd_earlier")
    visit = replace(visit, artifacts=(*visit.artifacts, row))
    assert visit.watched_repo() is None


def test_the_watch_is_none_when_the_no_code_marker_names_another_declaration():
    visit = _a_b_c()
    name, data = no_code_marker(
        {"repo": _WIDGET, "record": "re_widget", "base_branch": "main", "branch": "feat/x", "commit": "d" * 40}
    )
    row = _row(ArtifactKind.ASSET, name, data)
    visit = replace(visit, markers=(*visit.markers, row), artifacts=(*visit.artifacts, row))
    assert visit.watched_repo() is None


def test_the_watch_is_none_after_a_current_epoch_reopen_of_the_parked_repository():
    visit = _with_no_code(_a_b_c(), _WIDGET)
    reopen = _row(ArtifactKind.ASSET, f"park-reopen/{_GADGET}/2", "")
    assert replace(visit, markers=(*visit.markers, reopen)).watched_repo() is None


def _merged(visit: Visit, *, head: str = _SUBMITTED, base: str = "main", revision: str | None = "merged"):
    return replace(
        change(_WIDGET, 1, branch="feat/x", sha=head, base=base, state=ChangeState.MERGED, merged_revision=revision),
        closed_at=visit.now,
    )


def test_changed_head_lands_at_the_merged_revision_with_changed_head_evidence():
    visit = parked_visit()
    for head in ("augmented", "replaced"):
        forge = _forge()
        forge.repo(_WIDGET).changes[1] = _merged(visit, head=head)
        answer, sink = _land(forge, replace(visit, markers=(marker(visit),)))
        assert answer.choice == "landed"
        assert [row.revision for row in sink.landings()] == ["merged"]
        evidence = json.loads(sink.markers()["delivery-findings"])
        assert evidence["merged_head"] == head and evidence["submitted"] == _SUBMITTED
        assert "person_added_commits" not in evidence


def test_a_refused_annotation_never_costs_the_landing():
    visit = parked_visit()
    forge = _forge()
    forge.repo(_WIDGET).changes[1] = _merged(visit, head="augmented")
    sink = _Sink(refuse=("delivery-pr", "awaiting-external-merge", "delivery-findings"))
    answer, sink = _land(forge, visit, sink)
    assert answer.choice == "landed"
    assert [row.revision for row in sink.landings()] == ["merged"]


def test_a_landing_needs_neither_compare_nor_the_work_branch():
    visit = parked_visit()
    forge = _forge()
    forge.repo(_WIDGET).changes[1] = _merged(visit, head="augmented")
    forge.repo(_WIDGET).heads.clear()
    answer, sink = _land(forge, visit, offered=frozenset({Capability.CHANGES_OPEN, Capability.CHANGES_READ}), sink=None)
    assert answer.choice == "landed"
    assert [row.revision for row in sink.landings()] == ["merged"]
    assert not forge.named("compare") and not forge.named("branch_tip")


def test_a_merged_change_into_another_base_or_without_a_revision_does_not_land():
    visit = parked_visit()
    current = replace(visit, markers=(marker(visit),))
    for merged in (_merged(visit, base="release"), _merged(visit, revision=None)):
        forge = _forge()
        forge.repo(_WIDGET).changes[1] = merged
        answer, sink = _land(forge, current)
        assert not sink.landings()
        assert answer.choice in {"awaiting-merge", "pending"}


def test_a_merge_seen_past_the_fence_keeps_history_and_touches_no_later_repository():
    visit = parked_visit((_WIDGET, _GADGET))
    forge = _forge(_WIDGET, _GADGET)
    forge.repo(_WIDGET).changes[1] = _merged(visit)
    held = _Sink(fence_landings=True)
    answer, sink = _land(forge, replace(visit, markers=(marker(visit),)), held)
    assert answer.choice == "failure"
    assert [row.repo for row in sink.landings()] == [_WIDGET]
    assert all(repo == _WIDGET for _, repo, _ in forge.calls)
    assert "delivery-findings" not in sink.markers()


def _contained(forge: GitHubDouble) -> GitHubDouble:
    state = forge.repo(_WIDGET)
    state.base_tip = "base-tip"
    for pair in (("main", _SUBMITTED), ("main", "feat/x")):
        state.compares[pair] = comparison(ComparisonStatus.BEHIND)
    return forge


def test_park_resolves_contained_work_as_no_code_only_where_its_forge_can_prove_it():
    forge = _contained(_forge())
    answer, sink = _land(forge, parked_visit())
    assert answer.choice == "landed"
    assert sink.landings() == []
    assert any(name.startswith("no-code/") for name in sink.markers())
    assert not forge.named("open_change")

    minimal = _contained(_forge())
    answer, sink = _land(minimal, parked_visit(), offered=frozenset({Capability.CHANGES_OPEN, Capability.CHANGES_READ}))
    assert answer.choice == "awaiting-merge"
    assert not minimal.named("compare") and not minimal.named("branch_tip")
    assert not any(name.startswith("no-code/") for name in sink.markers())


def test_park_does_not_treat_contained_work_under_a_foreign_head_as_no_code():
    forge = _contained(_forge())
    state = forge.repo(_WIDGET)
    state.heads["feat/x"] = "foreign"
    state.compares[(_SUBMITTED, "foreign")] = comparison(
        ComparisonStatus.AHEAD, commits=(CommitSummary("foreign", (_SUBMITTED,)),)
    )
    answer, sink = _land(forge, parked_visit())
    assert answer.choice == "awaiting-merge"
    assert not any(name.startswith("no-code/") for name in sink.markers())
