"""The cadence errands (unit tier): resolution under each kind of chore and the instant each joined, the last run's
state and its three anchors, and the due rule at every edge — spacing, schedule, both, a live or settled last run,
a missed moment, and a never-run errand."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.cadence_attention import AttentionReason, ErrandState
from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.run_mode import RunMode
from blizzard.foundation.work_items import WorkItemClosure
from blizzard.hub.domain.cadence.errands import (
    Errand,
    ErrandKey,
    Holder,
    LastRun,
    RunOutcome,
    chore_errands,
    due_at,
    eligible_at,
    errand_state,
    errands,
    holder_of,
    is_due,
)
from blizzard.hub.domain.cadence.model import (
    Anchor,
    Attention,
    AttentionFacts,
    Cadence,
    Chore,
    ChoreSpec,
    Rhythm,
    ScopeSelection,
)
from blizzard.hub.domain.chunk.model import ChunkFacts
from blizzard.hub.domain.garden.routines import Routine
from blizzard.hub.domain.garden.scopes import Scope

_AT = datetime(2026, 11, 2, 12, tzinfo=UTC)  # a Monday, noon UTC
_PROJECT = "proj_test"
_CRON = "0 19 * * *"  # every evening at 19:00 UTC


def _routine(slug: str = "sweep", *, default: str = "main") -> Routine:
    return Routine(f"rtn_{slug}", slug, slug, "gardening", default, _AT, _PROJECT)


def _scope(slug: str, *, retired: bool = False) -> Scope:
    return Scope(f"scp_{slug}", slug, "", _AT, _PROJECT, retired=retired)


def _facts(
    routines: Sequence[Routine] = (),
    scopes: Sequence[Scope] = (),
    linked: Mapping[str, Collection[str]] | None = None,
    linked_at: Mapping[str, Mapping[str, datetime]] | None = None,
) -> AttentionFacts:
    return AttentionFacts(
        routines={r.slug: r for r in routines},
        scopes={s.slug: s for s in scopes},
        linked=linked or {},
        enabled_graphs=("gardening",),
        linked_at=linked_at or {},
    )


def _chore(
    selection: ScopeSelection = ScopeSelection.DEFAULT,
    scopes: tuple[str, ...] = (),
    *,
    key: str = "nightly",
    routine: str = "sweep",
    set_at: datetime = _AT,
) -> Chore:
    spec = ChoreSpec(key, routine, selection, scopes, RunMode.FULL, None, True)
    return Chore(f"chr_{key}", spec, set_at)


def _rhythm(*, cron: str | None = None, spacing: str | None = None, anchor: str | None = None) -> Rhythm:
    return Rhythm.parse(
        cron=cron, zone="UTC" if cron else None, has_schedule=cron is not None, spacing=spacing, anchor=anchor, at=_AT
    )


def _errand(joined_at: datetime = _AT) -> Errand:
    return Errand("cad_test", _chore(), "scp_main", "main", joined_at)


def _done(
    minted_at: datetime, *, started: timedelta = timedelta(minutes=5), took: timedelta = timedelta(hours=1)
) -> LastRun:
    return LastRun(minted_at, holder=Holder(ChunkStatus.DONE, minted_at + started, minted_at + started + took))


_LINKED = {"rtn_sweep": ["main", "a", "b"]}
_SCOPES = [_scope("main"), _scope("a"), _scope("b")]


# --- the errands ---


def test_an_all_chore_is_one_errand_per_live_swept_scope_keyed_by_chore_and_scope() -> None:
    facts = _facts([_routine()], [*_SCOPES[:2], _scope("b", retired=True)], _LINKED)
    found = chore_errands("cad_test", _chore(ScopeSelection.ALL), facts)
    assert [e.key for e in found] == [ErrandKey("chr_nightly", "scp_a"), ErrandKey("chr_nightly", "scp_main")]


def test_an_all_chore_sweeps_the_default_scope_even_unlinked() -> None:
    facts = _facts([_routine()], _SCOPES, {})
    assert [e.scope_slug for e in chore_errands("cad_test", _chore(ScopeSelection.ALL), facts)] == ["main"]


def test_a_listed_chore_is_one_errand_per_known_listed_scope_each_standing_on_its_own() -> None:
    facts = _facts([_routine()], [*_SCOPES, _scope("old", retired=True)], _LINKED)
    found = chore_errands("cad_test", _chore(ScopeSelection.LISTED, ("a", "gone", "old")), facts)
    assert [e.scope_slug for e in found] == ["a", "old"]


def test_an_absent_selection_is_one_errand_over_the_default_scope() -> None:
    facts = _facts([_routine()], _SCOPES, _LINKED)
    assert [e.key for e in chore_errands("cad_test", _chore(), facts)] == [ErrandKey("chr_nightly", "scp_main")]


def test_a_chore_naming_an_unknown_routine_has_no_errands() -> None:
    assert chore_errands("cad_test", _chore(routine="nope"), _facts([_routine()], _SCOPES, _LINKED)) == ()


def test_a_cadence_errands_follow_its_chore_order() -> None:
    cadence = Cadence(
        "cad_test",
        "c",
        "c",
        _rhythm(spacing="1d"),
        (_chore(key="b-chore"), _chore(ScopeSelection.LISTED, ("a",), key="a-chore")),
        _AT,
        _PROJECT,
    )
    found = errands(cadence, _facts([_routine()], _SCOPES, _LINKED))
    assert [(e.cadence_id, e.chore.key, e.scope_slug) for e in found] == [
        ("cad_test", "b-chore", "main"),
        ("cad_test", "a-chore", "a"),
    ]


def test_an_errand_is_stopped_by_its_cadence_its_chore_or_its_own_scope_only() -> None:
    errand = Errand("cad_test", _chore(ScopeSelection.LISTED, ("a", "b")), "scp_a", "a", _AT)
    assert errand.stopped_by([Attention(AttentionReason.NO_CHORES)])
    assert errand.stopped_by([Attention(AttentionReason.ROUTINE_RETIRED, "nightly")])
    assert errand.stopped_by([Attention(AttentionReason.SCOPE_RETIRED, "nightly", "a")])
    assert not errand.stopped_by([Attention(AttentionReason.SCOPE_RETIRED, "nightly", "b")])
    assert not errand.stopped_by([Attention(AttentionReason.ROUTINE_RETIRED, "other")])


# --- the joined instant ---


def test_an_all_errand_joins_at_the_later_of_its_chore_and_its_link() -> None:
    later = _AT + timedelta(days=1)
    facts = _facts([_routine()], _SCOPES, _LINKED, {"rtn_sweep": {"a": later, "b": _AT - timedelta(days=9)}})
    joined = {e.scope_slug: e.joined_at for e in chore_errands("cad_test", _chore(ScopeSelection.ALL), facts)}
    assert joined == {"a": later, "b": _AT, "main": _AT}  # main's link is unstamped: linked before the chore


@pytest.mark.parametrize(
    ("selection", "scopes"), [(ScopeSelection.LISTED, ("a",)), (ScopeSelection.DEFAULT, ())], ids=["listed", "default"]
)
def test_a_listed_or_default_errand_joins_when_its_chore_set_its_scopes(
    selection: ScopeSelection, scopes: tuple[str, ...]
) -> None:
    facts = _facts([_routine()], _SCOPES, _LINKED, {"rtn_sweep": {"a": _AT + timedelta(days=3)}})
    set_at = _AT - timedelta(hours=2)
    (errand,) = chore_errands("cad_test", _chore(selection, scopes, set_at=set_at), facts)
    assert errand.joined_at == set_at


def test_a_chore_edited_to_another_routine_keeps_its_errand_keys_and_so_its_last_runs() -> None:
    facts = _facts([_routine(), _routine("other")], _SCOPES, {})
    before = chore_errands("cad_test", _chore(), facts)
    after = chore_errands("cad_test", _chore(routine="other"), facts)
    assert [e.key for e in before] == [e.key for e in after]
    last = _done(_AT)
    runs = {before[0].key: last}
    rhythm = _rhythm(spacing="1d")
    assert not is_due(rhythm, after[0], runs.get(after[0].key), _AT + timedelta(hours=12))


# --- the last run's state ---


@pytest.mark.parametrize(
    ("run", "outcome"),
    [
        (LastRun(_AT, WorkItemClosure.WITHDRAWN, _AT, None), RunOutcome.CANCELLED),
        (LastRun(_AT, WorkItemClosure.WITHDRAWN, _AT, Holder(ChunkStatus.RUNNING)), RunOutcome.CANCELLED),
        (LastRun(_AT, WorkItemClosure.DELIVERED, _AT, Holder(ChunkStatus.RUNNING)), RunOutcome.FINISHED),
        (LastRun(_AT, holder=Holder(ChunkStatus.DONE)), RunOutcome.FINISHED),
        (LastRun(_AT, holder=Holder(ChunkStatus.STOPPED)), RunOutcome.CANCELLED),
        (LastRun(_AT, holder=Holder(ChunkStatus.RUNNING)), RunOutcome.LIVE),
        (LastRun(_AT, holder=Holder(ChunkStatus.NEEDS_HUMAN)), RunOutcome.LIVE),
        (LastRun(_AT, holder=Holder(ChunkStatus.NOT_READY)), RunOutcome.LIVE),
        (LastRun(_AT), RunOutcome.LIVE),
    ],
    ids=["withdrawn", "withdrawn-over-holder", "delivered", "done", "stopped", "running", "needs-human",
         "not-ready", "unresolvable-holder"],
)  # fmt: skip
def test_a_run_reads_withdrawn_then_delivered_then_its_holder(run: LastRun, outcome: RunOutcome) -> None:
    assert run.outcome() is outcome


# --- the anchors ---


def test_each_anchor_reads_its_moment_of_the_run() -> None:
    run = LastRun(_AT, holder=Holder(ChunkStatus.DONE, _AT + timedelta(minutes=5), _AT + timedelta(hours=1)))
    assert run.anchored_at(Anchor.SCHEDULED) == _AT
    assert run.anchored_at(Anchor.STARTED) == _AT + timedelta(minutes=5)
    assert run.anchored_at(Anchor.FINISHED) == _AT + timedelta(hours=1)


def test_a_run_stopped_before_it_started_reads_started_as_scheduled() -> None:
    run = LastRun(_AT, holder=Holder(ChunkStatus.STOPPED, None, _AT + timedelta(minutes=30)))
    assert run.anchored_at(Anchor.STARTED) == _AT
    assert run.anchored_at(Anchor.FINISHED) == _AT + timedelta(minutes=30)


def test_a_withdrawn_run_with_no_holder_reads_started_as_scheduled_and_finished_as_withdrawn() -> None:
    run = LastRun(_AT, WorkItemClosure.WITHDRAWN, _AT + timedelta(minutes=10))
    assert run.anchored_at(Anchor.STARTED) == _AT
    assert run.anchored_at(Anchor.FINISHED) == _AT + timedelta(minutes=10)


def test_finished_reads_the_earlier_of_the_holder_settling_and_the_item_closing() -> None:
    done = Holder(ChunkStatus.DONE, _AT, _AT + timedelta(hours=2))
    run = LastRun(_AT, WorkItemClosure.DELIVERED, _AT + timedelta(hours=3), done)
    assert run.anchored_at(Anchor.FINISHED) == _AT + timedelta(hours=2)
    delivered_first = LastRun(_AT, WorkItemClosure.DELIVERED, _AT + timedelta(hours=1), done)
    assert delivered_first.anchored_at(Anchor.FINISHED) == _AT + timedelta(hours=1)


def test_a_live_run_has_no_finished_instant_and_no_eligibility() -> None:
    live = LastRun(_AT, holder=Holder(ChunkStatus.RUNNING, _AT))
    assert live.anchored_at(Anchor.FINISHED) is None
    assert eligible_at(_rhythm(spacing="1h", anchor="finished"), live) is None


@pytest.mark.parametrize(
    ("anchor", "eligible"),
    [("scheduled", _AT + timedelta(days=1)), ("started", _AT + timedelta(days=1, minutes=5)),
     ("finished", _AT + timedelta(days=1, minutes=65))],
)  # fmt: skip
def test_the_spacing_counts_from_the_anchor(anchor: str, eligible: datetime) -> None:
    assert eligible_at(_rhythm(spacing="1d", anchor=anchor), _done(_AT)) == eligible


# --- a spacing alone ---


def test_a_spacing_alone_with_no_last_run_is_due_at_once() -> None:
    assert is_due(_rhythm(spacing="10d"), _errand(), None, _AT)


def test_a_spacing_alone_is_due_once_now_reaches_eligibility() -> None:
    rhythm, last = _rhythm(spacing="10d", anchor="finished"), _done(_AT)
    eligible = _AT + timedelta(days=10, minutes=65)
    assert not is_due(rhythm, _errand(), last, eligible - timedelta(hours=1))
    assert is_due(rhythm, _errand(), last, eligible)


# --- a schedule alone ---


def test_a_schedule_alone_is_due_when_the_latest_moment_falls_after_the_last_run() -> None:
    rhythm, last = _rhythm(cron=_CRON), _done(_AT.replace(hour=19))
    assert not is_due(rhythm, _errand(), last, _AT.replace(hour=23))  # latest moment is the run's own
    assert is_due(rhythm, _errand(), last, _AT.replace(hour=19) + timedelta(days=1))  # exactly on the moment


def test_a_schedule_alone_is_not_due_when_the_latest_moment_falls_before_the_reference() -> None:
    last = _done(_AT.replace(hour=19, minute=30))
    assert not is_due(_rhythm(cron=_CRON), _errand(), last, _AT.replace(hour=23))


# --- both ---


def test_both_wait_for_the_first_moment_at_or_past_eligibility() -> None:
    rhythm = _rhythm(cron=_CRON, spacing="36h", anchor="scheduled")
    last = _done(_AT.replace(hour=19))  # eligible from Wednesday 07:00
    tuesday = _AT.replace(hour=19) + timedelta(days=1, minutes=1)
    assert not is_due(rhythm, _errand(), last, tuesday)  # Tuesday's moment falls before eligibility
    assert not is_due(rhythm, _errand(), last, tuesday + timedelta(hours=13))  # eligible, but no moment since
    assert is_due(rhythm, _errand(), last, tuesday + timedelta(days=1))  # Wednesday's moment


def test_a_spacing_that_passed_while_the_run_was_live_is_due_once_it_settles() -> None:
    rhythm = _rhythm(spacing="1h", anchor="scheduled")
    now = _AT + timedelta(hours=5)
    assert not is_due(rhythm, _errand(), LastRun(_AT, holder=Holder(ChunkStatus.RUNNING, _AT)), now)
    assert is_due(rhythm, _errand(), LastRun(_AT, holder=Holder(ChunkStatus.DONE, _AT, now)), now)


# --- one live run ---


@pytest.mark.parametrize(
    "status", [ChunkStatus.READY, ChunkStatus.RUNNING, ChunkStatus.NEEDS_HUMAN, ChunkStatus.PAUSED]
)
def test_a_live_last_run_holds_its_errand_past_every_moment(status: ChunkStatus) -> None:
    last = LastRun(_AT, holder=Holder(status, _AT))
    week = _AT + timedelta(days=7)
    assert not is_due(_rhythm(cron=_CRON, spacing="1h"), _errand(), last, week)
    assert not is_due(_rhythm(spacing="1h"), _errand(), last, week)


def test_an_unresolvable_holder_holds_its_errand_rather_than_reading_as_never_run() -> None:
    assert not is_due(_rhythm(spacing="1m"), _errand(), LastRun(_AT), _AT + timedelta(days=30))


def test_a_stopped_last_run_releases_its_errand() -> None:
    last = LastRun(_AT, holder=Holder(ChunkStatus.STOPPED, None, _AT + timedelta(minutes=1)))
    assert is_due(_rhythm(spacing="1h"), _errand(), last, _AT + timedelta(hours=1))


def test_a_deleted_run_settles_as_withdrawn_and_waits_for_its_next_moment() -> None:
    rhythm = _rhythm(cron=_CRON)
    minted = _AT.replace(hour=19)
    deleted = LastRun(minted, WorkItemClosure.WITHDRAWN, minted + timedelta(minutes=1))
    assert not is_due(rhythm, _errand(), deleted, minted + timedelta(minutes=2))
    assert is_due(rhythm, _errand(), deleted, minted + timedelta(days=1))


def test_a_grouped_run_is_held_until_its_survivor_settles() -> None:
    survivor = Holder(ChunkStatus.RUNNING, _AT + timedelta(minutes=3))
    grouped = LastRun(_AT, holder=survivor)
    rhythm = _rhythm(spacing="1h", anchor="started")
    later = _AT + timedelta(hours=3)
    assert not is_due(rhythm, _errand(), grouped, later)
    settled = replace(grouped, holder=replace(survivor, status=ChunkStatus.DONE, completed_at=later))
    assert settled.anchored_at(Anchor.STARTED) == _AT + timedelta(minutes=3)  # the survivor's start
    assert is_due(rhythm, _errand(), settled, later)


def test_several_errands_sharing_one_chunk_each_wait_on_it_and_each_release_with_it() -> None:
    found = chore_errands("cad_test", _chore(ScopeSelection.ALL), _facts([_routine()], _SCOPES, _LINKED))
    assert len(found) == 3
    shared = Holder(ChunkStatus.RUNNING, _AT)
    runs = {e.key: LastRun(_AT + timedelta(seconds=i), holder=shared) for i, e in enumerate(found)}
    rhythm, later = _rhythm(spacing="1h"), _AT + timedelta(hours=2)
    assert not any(is_due(rhythm, e, runs[e.key], later) for e in found)
    done = replace(shared, status=ChunkStatus.DONE, completed_at=later)
    assert all(is_due(rhythm, e, replace(runs[e.key], holder=done), later) for e in found)


# --- a missed moment fires late, once ---


def test_a_moment_missed_across_a_restart_is_due_on_the_first_pass_after_it_then_not_again() -> None:
    rhythm = _rhythm(cron=_CRON)
    last = _done(_AT.replace(hour=19) - timedelta(days=1))
    back = _AT.replace(hour=19, minute=5)
    assert not is_due(rhythm, _errand(), last, _AT.replace(hour=18, minute=59))
    assert is_due(rhythm, _errand(), last, back)
    assert not is_due(rhythm, _errand(), _done(back), back + timedelta(minutes=1))


def test_a_week_away_is_owed_one_run_not_one_per_moment() -> None:
    rhythm = _rhythm(cron=_CRON)
    back = _AT + timedelta(days=7)
    assert is_due(rhythm, _errand(), _done(_AT - timedelta(days=1)), back)
    assert not is_due(rhythm, _errand(), _done(back), back + timedelta(minutes=1))


# --- an errand that has never run ---


@pytest.mark.parametrize(
    ("selection", "scopes"),
    [(ScopeSelection.ALL, ()), (ScopeSelection.LISTED, ("a",)), (ScopeSelection.DEFAULT, ())],
    ids=["all", "listed", "default"],
)
def test_a_never_run_errand_waits_for_the_first_moment_after_it_joined(
    selection: ScopeSelection, scopes: tuple[str, ...]
) -> None:
    joined = _AT.replace(hour=20)  # after Monday's 19:00 moment
    facts = _facts([_routine()], _SCOPES, _LINKED)
    errand = chore_errands("cad_test", _chore(selection, scopes, set_at=joined), facts)[0]
    rhythm = _rhythm(cron=_CRON)
    assert not is_due(rhythm, errand, None, joined + timedelta(hours=1))
    assert is_due(rhythm, errand, None, joined + timedelta(hours=23))


def test_a_scope_linked_into_an_all_chore_is_swept_that_evening() -> None:
    linked = _AT.replace(hour=9) + timedelta(days=1)  # Tuesday morning
    facts = _facts([_routine()], _SCOPES, _LINKED, {"rtn_sweep": {"b": linked}})
    found = chore_errands("cad_test", _chore(ScopeSelection.ALL, set_at=_AT - timedelta(days=30)), facts)
    (new,) = [e for e in found if e.scope_slug == "b"]
    evening = linked.replace(hour=19, minute=1)
    for rhythm in (_rhythm(cron=_CRON), _rhythm(cron=_CRON, spacing="10d")):
        assert not is_due(rhythm, new, None, linked + timedelta(hours=1))
        assert is_due(rhythm, new, None, evening)


def test_a_never_run_errand_under_a_spacing_beside_its_schedule_waits_only_for_the_moment() -> None:
    rhythm = _rhythm(cron=_CRON, spacing="10d")
    errand = _errand(joined_at=_AT)
    assert not is_due(rhythm, errand, None, _AT + timedelta(hours=1))
    assert is_due(rhythm, errand, None, _AT.replace(hour=19))


# --- when an errand is due, and where it stands ---

_NOW = _AT + timedelta(days=3)


def _holds_at_due_at(rhythm: Rhythm, errand: Errand, last_run: LastRun | None) -> datetime:
    """``due_at`` of the case, held to be the first instant the rule holds."""
    at = due_at(rhythm, errand, last_run)
    assert at is not None
    assert is_due(rhythm, errand, last_run, at)
    assert not is_due(rhythm, errand, last_run, at - timedelta(seconds=1))
    return at


def test_a_never_run_spacing_alone_is_due_at_the_instant_it_joined() -> None:
    joined = _AT + timedelta(hours=2)
    assert _holds_at_due_at(_rhythm(spacing="1d"), _errand(joined), None) == joined


def test_a_spacing_alone_is_due_at_eligibility() -> None:
    run = _done(_AT)
    rhythm = _rhythm(spacing="1d", anchor="finished")
    assert _holds_at_due_at(rhythm, _errand(), run) == eligible_at(rhythm, run)


def test_a_schedule_alone_is_due_at_the_first_moment_after_the_reference() -> None:
    assert _holds_at_due_at(_rhythm(cron=_CRON), _errand(), None) == _AT.replace(hour=19)
    run = _done(_AT.replace(hour=19))
    assert _holds_at_due_at(_rhythm(cron=_CRON), _errand(), run) == _AT.replace(hour=19) + timedelta(days=1)


def test_a_schedule_and_a_spacing_are_due_at_the_first_moment_at_or_after_eligibility() -> None:
    run = _done(_AT.replace(hour=19))
    rhythm = _rhythm(cron=_CRON, spacing="30h", anchor="scheduled")
    eligible = eligible_at(rhythm, run)
    assert eligible == _AT.replace(hour=19) + timedelta(hours=30)  # 01:00, between two evening moments
    assert _holds_at_due_at(rhythm, _errand(), run) == _AT.replace(hour=19) + timedelta(days=2)


def test_a_moment_that_is_exactly_eligibility_is_the_due_instant() -> None:
    run = _done(_AT.replace(hour=19))
    assert _holds_at_due_at(_rhythm(cron=_CRON, spacing="1d", anchor="scheduled"), _errand(), run) == (
        _AT.replace(hour=19) + timedelta(days=1)
    )


def test_a_missed_moment_is_due_in_the_past() -> None:
    assert due_at(_rhythm(cron=_CRON), _errand(), _done(_AT.replace(hour=19))) < _NOW  # type: ignore[operator]


def test_a_live_last_run_has_no_due_instant() -> None:
    assert due_at(_rhythm(spacing="1d"), _errand(), LastRun(_AT)) is None


def _state(
    last_run: LastRun | None,
    *,
    stopping: Sequence[Attention] = (),
    retired: bool = False,
    rhythm: Rhythm | None = None,
    now: datetime = _NOW,
) -> ErrandState:
    return errand_state(
        rhythm or _rhythm(spacing="1d"), _errand(), last_run, stopping=stopping, retired=retired, now=now
    )


def test_an_errand_reads_due_running_or_waiting() -> None:
    assert _state(None) is ErrandState.DUE
    assert _state(LastRun(_AT)) is ErrandState.RUNNING
    assert _state(_done(_NOW - timedelta(hours=2))) is ErrandState.WAITING


def test_attention_stops_an_errand_even_while_its_last_run_is_live() -> None:
    stop = Attention(AttentionReason.SCOPE_RETIRED, "nightly", "main")
    assert _state(LastRun(_AT), stopping=[stop]) is ErrandState.STOPPED
    assert _state(None, stopping=[stop]) is ErrandState.STOPPED


def test_attention_on_another_scope_does_not_stop_the_errand() -> None:
    assert _state(None, stopping=[Attention(AttentionReason.SCOPE_RETIRED, "nightly", "other")]) is ErrandState.DUE


def test_a_retired_cadence_stops_every_errand() -> None:
    assert _state(None, retired=True) is ErrandState.STOPPED
    assert _state(LastRun(_AT), retired=True) is ErrandState.STOPPED


def test_the_holder_of_a_run_names_its_chunk() -> None:
    held = {"chk_a": ChunkFacts(minted=True), "chk_b": ChunkFacts(minted=True)}
    holder = holder_of(held)
    assert holder is not None
    assert holder.chunk_id == "chk_b"
