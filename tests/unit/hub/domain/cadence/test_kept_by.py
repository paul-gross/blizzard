"""Which chores keep a routine (unit tier): one row per chore naming it on an enabled cadence of its project, in
cadence then chore order, each carrying its cadence's next scheduled moment."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from blizzard.foundation.run_mode import RunMode
from blizzard.hub.domain.cadence.kept_by import KeptBy, kept_by
from blizzard.hub.domain.cadence.model import Cadence, Chore, ChoreSpec, Rhythm, ScopeSelection
from blizzard.hub.domain.garden.routines import Routine

_AT = datetime(2026, 11, 2, 12, tzinfo=UTC)
_PROJECT = "proj_test"


def _routine(slug: str = "sweep", *, project: str = _PROJECT, retired: bool = False) -> Routine:
    return Routine(f"rtn_{slug}", slug, slug, "gardening", "main", _AT, project, retired=retired)


def _chore(key: str, routine: str = "sweep") -> Chore:
    return Chore(f"chr_{key}", ChoreSpec(key, routine, ScopeSelection.DEFAULT, (), RunMode.FULL, None, True), _AT)


def _cadence(
    slug: str, chores: tuple[Chore, ...], *, cron: str | None = None, project: str = _PROJECT, retired: bool = False
) -> Cadence:
    rhythm = Rhythm.parse(
        cron=cron,
        zone="UTC" if cron else None,
        has_schedule=cron is not None,
        spacing=None if cron else "1d",
        anchor=None,
        at=_AT,
    )
    return Cadence(f"cad_{slug}", slug, slug.title(), rhythm, chores, _AT, project, retired=retired)


def test_a_chore_naming_the_routine_keeps_it_with_the_next_scheduled_moment() -> None:
    cadence = _cadence("evening", (_chore("a"),), cron="0 19 * * *")

    assert kept_by(_routine(), [cadence], _AT) == (
        KeptBy("evening", "Evening", "a", datetime(2026, 11, 2, 19, tzinfo=UTC)),
    )


def test_a_cadence_with_no_schedule_keeps_with_no_next_moment() -> None:
    assert kept_by(_routine(), [_cadence("spaced", (_chore("a"),))], _AT)[0].next_moment is None


def test_rows_follow_cadence_order_then_chore_order_one_per_matching_chore() -> None:
    first = _cadence("one", (_chore("x"), _chore("other", "elsewhere"), _chore("y")))
    second = _cadence("two", (_chore("z"),))

    rows = kept_by(_routine(), [first, second], _AT)

    assert [(r.cadence_slug, r.chore_key) for r in rows] == [("one", "x"), ("one", "y"), ("two", "z")]


def test_a_retired_cadence_does_not_keep() -> None:
    assert kept_by(_routine(), [_cadence("gone", (_chore("a"),), retired=True)], _AT) == ()


def test_a_cadence_of_another_project_does_not_keep() -> None:
    assert kept_by(_routine(), [_cadence("far", (_chore("a"),), project="proj_other")], _AT) == ()


def test_a_retired_routine_is_still_kept() -> None:
    assert len(kept_by(_routine(retired=True), [_cadence("c", (_chore("a"),))], _AT)) == 1


def test_a_routine_no_chore_names_is_kept_by_nothing() -> None:
    assert kept_by(_routine("lonely"), [_cadence("c", (_chore("a"),))], _AT) == ()
    assert kept_by(replace(_routine(), slug="sweep"), [], _AT) == ()
