"""The cadence model (unit tier): every refusal validation names a field for, the chore merge's id keeping and
minting and ``scopes_set_at`` restamping, and every attention reason read against resolved routines and scopes."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest

from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.run_mode import RunMode
from blizzard.hub.domain.cadence.model import (
    Anchor,
    Attention,
    AttentionFacts,
    AttentionReason,
    Cadence,
    Chore,
    ChoreShape,
    ChoreSpec,
    Rhythm,
    ScopeSelection,
    Spacing,
    attention,
    merge_chores,
    parse_chores,
)
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.garden.routines import Routine
from blizzard.hub.domain.garden.scopes import Scope

_AT = datetime(2026, 11, 5, 12, tzinfo=UTC)
_LATER = _AT + timedelta(days=1)
_PROJECT = "proj_test"


def _rhythm(
    *,
    cron: str | None = "0 20 * * *",
    zone: str | None = "America/Chicago",
    has_schedule: bool = True,
    spacing: str | None = None,
    anchor: str | None = None,
) -> Rhythm:
    return Rhythm.parse(cron=cron, zone=zone, has_schedule=has_schedule, spacing=spacing, anchor=anchor, at=_AT)


# --- the rhythm ---


def test_a_schedule_alone_a_spacing_alone_and_both_are_each_a_rhythm() -> None:
    assert _rhythm().spacing is None
    alone = _rhythm(has_schedule=False, spacing="10d")
    assert alone.schedule is None and alone.spacing == Spacing(14400)
    both = _rhythm(spacing="36h", anchor="finished")
    assert both.schedule is not None and both.spacing == Spacing(2160) and both.anchor is Anchor.FINISHED


def test_an_unstated_anchor_reads_as_started_with_a_spacing_and_as_none_without_one() -> None:
    assert _rhythm(spacing="90m").effective_anchor() is Anchor.STARTED
    assert _rhythm().effective_anchor() is None


@pytest.mark.parametrize(("raw", "minutes"), [("1m", 1), ("90m", 90), ("36h", 2160), ("7d", 10080)])
def test_a_spacing_parses_to_whole_minutes(raw: str, minutes: int) -> None:
    assert Spacing.parse(raw).minutes == minutes


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"has_schedule": False}, "schedule"),
        ({"has_schedule": False, "anchor": "started"}, "schedule"),
        ({"anchor": "started"}, "anchor"),
        ({"spacing": "1d", "anchor": "minted"}, "anchor"),
        ({"cron": None}, "schedule.cron"),
        ({"cron": "0 0 30 2 *"}, "schedule.cron"),
        ({"cron": "0 0 * * * *"}, "schedule.cron"),
        ({"cron": "0 0 */30 2 sun#5"}, "schedule.cron"),
        ({"zone": None}, "schedule.zone"),
        ({"zone": "Mars/Base"}, "schedule.zone"),
        ({"spacing": "10"}, "spacing"),
        ({"spacing": "1w"}, "spacing"),
        ({"spacing": "1.5h"}, "spacing"),
        ({"spacing": "0m"}, "spacing"),
        ({"spacing": "0d"}, "spacing"),
    ],
)
def test_a_rhythm_that_cannot_mean_anything_is_refused_naming_its_field(
    kwargs: dict[str, str | bool | None], field: str
) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        _rhythm(**kwargs)  # pyright: ignore[reportArgumentType]
    assert caught.value.field == field


# --- the chores ---


def _shape(
    key: str | None = "arch",
    routine: str | None = "architecture-sweep",
    scopes: Literal["all"] | Sequence[str] | None = None,
    mode: str | None = None,
    note: str | None = None,
    promote: bool | None = None,
) -> ChoreShape:
    return ChoreShape(key, routine, scopes, mode, note, promote)


def test_a_chore_with_only_its_key_and_routine_takes_every_default() -> None:
    (spec,) = parse_chores([_shape()])
    assert spec == ChoreSpec("arch", "architecture-sweep", ScopeSelection.DEFAULT, (), RunMode.FULL, None, True)


def test_a_chore_states_all_scopes_or_a_list_in_the_order_written() -> None:
    every, listed = parse_chores([_shape(scopes="all", mode="delta"), _shape("tests", scopes=["b", "a"])])
    assert (every.selection, every.scopes, every.mode) == (ScopeSelection.ALL, (), RunMode.DELTA)
    assert (listed.selection, listed.scopes) == (ScopeSelection.LISTED, ("b", "a"))


def test_chores_keep_the_order_written() -> None:
    assert [spec.key for spec in parse_chores([_shape("b"), _shape("a")])] == ["b", "a"]


@pytest.mark.parametrize(
    ("shapes", "field"),
    [
        ([_shape(key=None)], "chores[0].key"),
        ([_shape(key="")], "chores[0].key"),
        ([_shape(routine=None)], "chores[0].routine"),
        ([_shape(), _shape(routine="other")], "chores[1].key"),
        ([_shape(key="Not A Slug")], "chores[0].key"),
        ([_shape(scopes=["ok", "Bad_Slug"])], "chores[0].scopes[1]"),
        ([_shape(scopes=[])], "chores[0].scopes"),
        ([_shape(scopes="every")], "chores[0].scopes"),
        ([_shape(mode="partial")], "chores[0].mode"),
    ],
)
def test_a_malformed_chore_is_refused_naming_its_field(shapes: list[ChoreShape], field: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        parse_chores(shapes)
    assert caught.value.field == field


# --- the chore merge ---


def _spec(key: str = "arch", **changes: object) -> ChoreSpec:
    base = ChoreSpec(key, "architecture-sweep", ScopeSelection.LISTED, ("a", "b"), RunMode.FULL, None, True)
    return replace(base, **changes)


def _stored(*specs: ChoreSpec) -> tuple[Chore, ...]:
    return tuple(Chore(Id.mint_at(IdPrefix.CHORE, _AT).value, spec, _AT) for spec in specs)


def test_a_new_key_mints_a_chore_id_stamped_at_the_write() -> None:
    merge = merge_chores((), [_spec()], at=_AT)
    (chore,) = merge.chores
    assert Id.parse(chore.chore_id) is not None and chore.chore_id.startswith("chr_")
    assert chore.scopes_set_at == _AT
    assert (merge.added, merge.removed, merge.changed) == (("arch",), (), ())


def test_a_kept_key_keeps_its_id_whatever_else_it_changes() -> None:
    (held,) = _stored(_spec())
    edited = _spec(routine_slug="test-gaps", mode=RunMode.DELTA, note="weigh it", promote=False)
    merge = merge_chores([held], [edited], at=_LATER)
    (chore,) = merge.chores
    assert chore.chore_id == held.chore_id and chore.spec == edited
    assert chore.scopes_set_at == _AT
    assert (merge.added, merge.removed, merge.changed) == ((), (), ("arch",))


def test_a_chore_restated_unchanged_is_kept_whole_and_reported_nowhere() -> None:
    (held,) = _stored(_spec())
    merge = merge_chores([held], [_spec(scopes=("b", "a"))], at=_LATER)
    assert merge.chores[0].scopes_set_at == _AT
    assert (merge.added, merge.removed) == ((), ())


@pytest.mark.parametrize(
    "edited",
    [
        _spec(scopes=("a", "c")),
        _spec(scopes=("a",)),
        _spec(selection=ScopeSelection.ALL, scopes=()),
        _spec(selection=ScopeSelection.DEFAULT, scopes=()),
    ],
)
def test_changing_the_named_scope_set_restamps_scopes_set_at(edited: ChoreSpec) -> None:
    (held,) = _stored(_spec())
    (chore,) = merge_chores([held], [edited], at=_LATER).chores
    assert chore.chore_id == held.chore_id and chore.scopes_set_at == _LATER


def test_a_dropped_key_is_removed_and_written_again_mints_a_new_id() -> None:
    held = _stored(_spec("arch"), _spec("tests"))
    dropped = merge_chores(held, [_spec("tests")], at=_LATER)
    assert [c.key for c in dropped.chores] == ["tests"] and dropped.removed == ("arch",)
    assert dropped.chores[0].chore_id == held[1].chore_id
    back = merge_chores(dropped.chores, [_spec("tests"), _spec("arch")], at=_LATER)
    assert back.added == ("arch",)
    assert back.chores[1].chore_id != held[0].chore_id and back.chores[1].scopes_set_at == _LATER


# --- attention ---


def _routine(slug: str = "architecture-sweep", *, retired: bool = False, graph: str = "gardening") -> Routine:
    return Routine(f"rtn_{slug}", slug, slug, graph, "main", _AT, _PROJECT, retired=retired)


def _scope(slug: str, *, retired: bool = False) -> Scope:
    return Scope(f"scp_{slug}", slug, "", _AT, _PROJECT, retired=retired)


def _facts(
    routines: Sequence[Routine] = (),
    scopes: Sequence[Scope] = (),
    linked: Mapping[str, Collection[str]] | None = None,
    graphs: Collection[str] = ("gardening",),
) -> AttentionFacts:
    return AttentionFacts(
        routines={r.slug: r for r in routines},
        scopes={s.slug: s for s in scopes},
        linked=linked or {},
        enabled_graphs=graphs,
    )


def _attention(*specs: ChoreSpec, facts: AttentionFacts, retired: bool = False) -> tuple[Attention, ...]:
    return attention(_stored(*specs), retired=retired, facts=facts)


_LINKED = {"rtn_architecture-sweep": ["main", "a", "b"]}
_LIVE = _facts([_routine()], [_scope("main"), _scope("a"), _scope("b")], _LINKED)


def test_a_cadence_whose_errands_can_all_run_needs_no_attention() -> None:
    assert _attention(_spec(), _spec("all", selection=ScopeSelection.ALL, scopes=()), facts=_LIVE) == ()


def test_a_cadence_keeping_no_chore_needs_attention_as_a_whole() -> None:
    assert attention((), retired=False, facts=_LIVE) == (Attention(AttentionReason.NO_CHORES),)


def test_a_retired_cadence_needs_no_attention() -> None:
    assert attention((), retired=True, facts=_LIVE) == ()
    assert _attention(_spec(routine_slug="nope"), facts=_LIVE, retired=True) == ()


def test_a_routine_no_record_carries_stops_its_chore() -> None:
    found = _attention(_spec(routine_slug="nope"), facts=_LIVE)
    assert found == (Attention(AttentionReason.ROUTINE_UNKNOWN, "arch"),)


def test_a_retired_routine_stops_its_chore() -> None:
    facts = replace(_LIVE, routines={"architecture-sweep": _routine(retired=True)})
    assert _attention(_spec(), facts=facts) == (Attention(AttentionReason.ROUTINE_RETIRED, "arch"),)


def test_a_routine_whose_graph_no_longer_resolves_stops_its_chore() -> None:
    facts = replace(_LIVE, enabled_graphs=())
    assert _attention(_spec(), facts=facts) == (Attention(AttentionReason.GRAPH_UNRESOLVED, "arch"),)


def test_a_listed_scope_unknown_stops_its_errand() -> None:
    found = _attention(_spec(scopes=("a", "ghost")), facts=_LIVE)
    assert found == (Attention(AttentionReason.SCOPE_UNKNOWN, "arch", "ghost"),)


def test_a_listed_scope_retired_stops_its_errand() -> None:
    facts = replace(_LIVE, scopes={**_LIVE.scopes, "b": _scope("b", retired=True)})
    assert _attention(_spec(), facts=facts) == (Attention(AttentionReason.SCOPE_RETIRED, "arch", "b"),)


def test_a_listed_scope_unlinked_beside_a_linked_one_stops_only_its_errand() -> None:
    facts = replace(_LIVE, scopes={**_LIVE.scopes, "c": _scope("c")})
    found = _attention(_spec(scopes=("a", "c")), facts=facts)
    assert found == (Attention(AttentionReason.SCOPE_UNLINKED, "arch", "c"),)


def test_the_routines_default_scope_retired_stops_a_chore_naming_none() -> None:
    facts = replace(_LIVE, scopes={**_LIVE.scopes, "main": _scope("main", retired=True)})
    default = _spec(selection=ScopeSelection.DEFAULT, scopes=())
    assert _attention(default, facts=facts) == (Attention(AttentionReason.SCOPE_RETIRED, "arch", "main"),)


def test_an_all_chore_whose_last_scope_is_retired_has_no_scopes() -> None:
    facts = _facts([_routine()], [_scope("main", retired=True)], {"rtn_architecture-sweep": ["main"]})
    every = _spec(selection=ScopeSelection.ALL, scopes=())
    assert _attention(every, facts=facts) == (Attention(AttentionReason.NO_SCOPES, "arch"),)


def test_an_all_chore_never_names_a_single_scope_that_left_its_round() -> None:
    facts = replace(_LIVE, scopes={**_LIVE.scopes, "a": _scope("a", retired=True)})
    assert _attention(_spec(selection=ScopeSelection.ALL, scopes=()), facts=facts) == ()


def test_each_chore_reports_its_own_attention_in_the_cadences_order() -> None:
    found = _attention(_spec("x", routine_slug="nope"), _spec("y", scopes=("ghost",)), facts=_LIVE)
    assert [(a.chore, a.reason) for a in found] == [
        ("x", AttentionReason.ROUTINE_UNKNOWN),
        ("y", AttentionReason.SCOPE_UNKNOWN),
    ]


# --- the record ---


def _cadence(*specs: ChoreSpec, retired: bool = False) -> Cadence:
    return Cadence("cad_x", "evening", "evening", _rhythm(), _stored(*specs), _AT, _PROJECT, retired=retired)


def test_a_cadence_admits_work_only_while_live_and_keeping_a_promoting_chore() -> None:
    assert _cadence(_spec(promote=False), _spec("b")).admits()
    assert not _cadence(_spec(promote=False)).admits()
    assert not _cadence().admits()
    assert not _cadence(_spec(), retired=True).admits()


def test_a_cadence_reads_its_attention_against_the_facts_it_is_handed() -> None:
    assert _cadence().attention(_LIVE) == (Attention(AttentionReason.NO_CHORES),)
