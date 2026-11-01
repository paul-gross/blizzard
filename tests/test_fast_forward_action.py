"""Fast-forward landing through the action and forge seams."""

from dataclasses import replace

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.hub.domain.config.landing_policies import LandingPolicy
from blizzard.hub.forge.landing import CheckOutcome, CommitSummary, ComparisonStatus, FileChange, ForgeRejected
from tests.github_double import RED, check, comparison
from tests.test_land_action import _SUBMITTED, _WIDGET, _forge, _land, _row, _Sink, _visit

pytestmark = pytest.mark.component


def _fast_visit():
    visit = _visit()
    targets = tuple(replace(target, landing_policy=LandingPolicy.FAST_FORWARD) for target in visit.resolution.targets)
    return replace(visit, resolution=replace(visit.resolution, targets=targets))


def _ahead():
    forge = _forge()
    forge.repo(_WIDGET).compares[("base-tip", _SUBMITTED)] = comparison(ComparisonStatus.AHEAD)
    forge.repo(_WIDGET).heads["main"] = "base-tip"
    return forge


def test_ahead_lands_proven_commit_without_a_change():
    forge = _ahead()
    answer, sink = _land(forge, _fast_visit())
    assert answer.choice == "landed"
    assert sink.landings()[0].revision == _SUBMITTED
    assert sink.markers()[f"fast-forward-candidate/{_WIDGET}/{_SUBMITTED}"] == _SUBMITTED
    assert forge.writes() == ["advance_ref"]


def test_no_checks_does_not_advance():
    forge = _ahead()
    forge.repo(_WIDGET).checks[_SUBMITTED] = ()
    answer, sink = _land(forge, _fast_visit())
    assert answer.choice == "pending"
    assert not sink.landings()
    assert not forge.writes()


def test_inherited_failure_uses_shared_rerun_rule():
    forge = _ahead()
    state = forge.repo(_WIDGET)
    state.checks[_SUBMITTED] = RED
    state.checks["base-tip"] = RED
    answer, sink = _land(forge, _fast_visit())
    assert answer.choice == "pending"
    assert forge.writes() == ["rerun_check"]
    assert f"ci-rerun/{_WIDGET}/build/{_SUBMITTED}" in sink.markers()


def test_inherited_failure_waits_without_a_rerun_while_a_check_is_pending():
    forge = _ahead()
    state = forge.repo(_WIDGET)
    state.checks[_SUBMITTED] = (*RED, check("e2e", CheckOutcome.PENDING, "in_progress", id="2"))
    state.checks["base-tip"] = RED
    answer, sink = _land(forge, _fast_visit())
    assert answer.choice == "pending"
    assert not forge.writes()
    assert not any(name.startswith("ci-rerun/") for name in sink.markers())


@pytest.mark.parametrize(
    "reason,cause", [(ForgeRejected.STALE, None), (ForgeRejected.PROTECTED, EscalationCause.BRANCH_PROTECTED)]
)
def test_advance_refusals(reason, cause):
    forge = _ahead()
    forge.repo(_WIDGET).replies["advance_ref"] = [ForgeRejected(reason)]
    answer, sink = _land(forge, _fast_visit())
    assert not sink.landings()
    if cause is None:
        assert answer.choice == "pending"
    else:
        assert answer.escalation is not None and answer.escalation.cause == cause


def test_stale_advance_rechecks_moved_base_before_retrying():
    forge = _ahead()
    state = forge.repo(_WIDGET)

    def move_base(**_kwargs):
        state.base_tip = "later-tip"
        state.heads["main"] = "later-tip"
        raise ForgeRejected(ForgeRejected.STALE)

    state.replies["advance_ref"] = [move_base]
    first, sink = _land(forge, _fast_visit())
    assert first.choice == "pending"
    assert not sink.landings()
    assert sink.markers()[f"fast-forward-candidate/{_WIDGET}/{_SUBMITTED}"] == _SUBMITTED

    # The old candidate does not contain the moved base. A new comparison is required
    # before the advance can be retried, even though the intent marker is durable.
    state.compares[(_SUBMITTED, "later-tip")] = comparison(ComparisonStatus.DIVERGED)
    state.compares[("later-tip", _SUBMITTED)] = comparison(ComparisonStatus.AHEAD)
    state.replies.pop("advance_ref")
    marker = _row(ArtifactKind.ASSET, f"fast-forward-candidate/{_WIDGET}/{_SUBMITTED}", _SUBMITTED)
    second, recovered = _land(forge, replace(_fast_visit(), markers=(marker,)))
    assert second.choice == "landed"
    assert recovered.landings()[0].revision == _SUBMITTED
    assert forge.named("advance_ref")[-1][1] == {
        "branch": "main",
        "expected": "later-tip",
        "revision": _SUBMITTED,
    }


def test_merge_method_refusal_escalates_instead_of_waiting():
    forge = _forge()
    forge.repo(_WIDGET).replies["merge"] = [ForgeRejected(ForgeRejected.METHOD)]
    answer, sink = _land(forge)
    assert answer.escalation is not None
    assert answer.escalation.cause == EscalationCause.MERGE_METHOD_REFUSED
    assert not sink.landings()


def test_diverged_merge_conflict_does_not_advance():
    forge = _ahead()
    state = forge.repo(_WIDGET)
    state.compares[("base-tip", _SUBMITTED)] = comparison(ComparisonStatus.DIVERGED)
    state.replies["merge_into"] = [ForgeRejected(ForgeRejected.CONFLICT)]
    answer, sink = _land(forge, _fast_visit())
    assert answer.choice == "conflict"
    assert forge.writes() == ["create_ref", "merge_into", "delete_ref"]
    assert not sink.landings()


def test_protected_landing_branch_escalates():
    forge = _ahead()
    state = forge.repo(_WIDGET)
    state.compares[("base-tip", _SUBMITTED)] = comparison(ComparisonStatus.DIVERGED)
    state.replies["create_ref"] = [ForgeRejected(ForgeRejected.PROTECTED)]
    answer, sink = _land(forge, _fast_visit())
    assert answer.escalation is not None
    assert answer.escalation.cause == EscalationCause.BRANCH_PROTECTED
    assert not sink.landings()


def test_recovery_records_proven_revision_not_later_base_tip():
    forge = _ahead()
    state = forge.repo(_WIDGET)
    state.base_tip = "later-tip"
    state.compares[(_SUBMITTED, "later-tip")] = comparison(ComparisonStatus.AHEAD)
    visit = _fast_visit()
    marker = _row(ArtifactKind.ASSET, f"fast-forward-candidate/{_WIDGET}/{_SUBMITTED}", _SUBMITTED)
    answer, sink = _land(forge, replace(visit, markers=(marker,)))
    assert answer.choice == "landed"
    assert sink.landings()[0].revision == _SUBMITTED
    assert not forge.writes()


def _diverged(candidate="candidate", parents=None):
    forge = _ahead()
    state = forge.repo(_WIDGET)
    state.heads["feat/x-landing"] = candidate
    state.compares[("base-tip", _SUBMITTED)] = comparison(ComparisonStatus.DIVERGED, merge_base="common")
    state.compares[("common", "base-tip")] = comparison(ComparisonStatus.AHEAD)
    state.compares[("base-tip", candidate)] = comparison(
        ComparisonStatus.AHEAD, commits=(CommitSummary(candidate, parents or ("base-tip", _SUBMITTED)),)
    )
    return forge


def test_reused_candidate_is_proven_then_cleaned_after_landing():
    forge = _diverged()
    answer, sink = _land(forge, _fast_visit())
    assert answer.choice == "landed"
    assert sink.landings()[0].revision == "candidate"
    assert forge.writes() == ["advance_ref", "delete_ref"]
    assert sink.markers()[f"fast-forward-candidate/{_WIDGET}/candidate"] == "candidate"


def test_cleanup_never_precedes_durable_landing():
    forge = _diverged()
    answer, _ = _land(forge, _fast_visit(), _Sink(fence_landings=True))
    assert answer.choice == "failure"
    assert forge.writes() == ["advance_ref"]


def test_foreign_candidate_is_rebuilt_not_advanced():
    forge = _diverged(parents=(_SUBMITTED, "base-tip"))
    forge.repo(_WIDGET).replies["merge_into"] = [ForgeRejected(ForgeRejected.CONFLICT)]
    answer, _ = _land(forge, _fast_visit())
    assert answer.choice == "conflict"
    assert forge.writes() == ["delete_ref", "create_ref", "merge_into", "delete_ref"]


def test_candidate_with_extra_content_is_not_reused():
    forge = _diverged()
    state = forge.repo(_WIDGET)
    foreign = FileChange("foreign.txt", None, "added", "blob", "+foreign", 1, 0)
    state.compares[("base-tip", "candidate")] = comparison(
        ComparisonStatus.AHEAD, commits=(CommitSummary("candidate", ("base-tip", _SUBMITTED)),), files=(foreign,)
    )
    state.replies["merge_into"] = [ForgeRejected(ForgeRejected.CONFLICT)]
    answer, _ = _land(forge, _fast_visit())
    assert answer.choice == "conflict"
    assert "advance_ref" not in forge.writes()
    assert forge.writes()[0] == "delete_ref"


def test_stale_intent_does_not_prevent_new_candidate_marker():
    forge = _ahead()
    state = forge.repo(_WIDGET)
    state.compares[("old-candidate", "base-tip")] = comparison(ComparisonStatus.DIVERGED)
    marker = _row(ArtifactKind.ASSET, f"fast-forward-candidate/{_WIDGET}/old-candidate", "old-candidate")
    answer, sink = _land(forge, replace(_fast_visit(), markers=(marker,)))
    assert answer.choice == "landed"
    assert sink.markers()[f"fast-forward-candidate/{_WIDGET}/{_SUBMITTED}"] == _SUBMITTED
