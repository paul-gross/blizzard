"""The urgency chain at every link, and the lock set a mint's chain names ahead of its unit, as pure values."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.project_colors import ProjectColor
from blizzard.hub.domain.urgency.chain import URGENCY_FIELD, ChainLinks, resolve_chain
from blizzard.hub.domain.urgency.model import Urgency, UrgencyId, UrgencyRetired
from blizzard.hub.domain.urgency.ports import tenant_settings_lock, urgency_lock
from blizzard.hub.domain.urgency.services import MintAhead

_AT = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)


def _urgency(slug: str, *, retired: bool = False) -> Urgency:
    return Urgency(
        urgency_id=UrgencyId(f"urg_{slug}"),
        slug=slug,
        name=slug,
        color=ProjectColor.SLATE,
        emoji=None,
        icon=None,
        created_at=_AT,
        created_by="usr_op",
        retired=retired,
    )


_DEFAULT = _urgency("standard")
_STATED = _urgency("stated")
_CHORE = _urgency("chore")
_ROUTINE = _urgency("routine")


def test_a_run_stating_an_urgency_takes_it_over_every_other_link() -> None:
    links = ChainLinks(stated=_STATED, chore=_CHORE, routine=_ROUTINE)

    assert resolve_chain(links, _DEFAULT) == _STATED


def test_a_chore_naming_one_outranks_the_routine_naming_another() -> None:
    assert resolve_chain(ChainLinks(chore=_CHORE, routine=_ROUTINE), _DEFAULT) == _CHORE


def test_a_routine_naming_one_outranks_the_default() -> None:
    assert resolve_chain(ChainLinks(routine=_ROUTINE), _DEFAULT) == _ROUTINE


def test_a_chore_and_a_routine_both_naming_none_fall_through_to_the_default() -> None:
    assert resolve_chain(ChainLinks(), _DEFAULT) == _DEFAULT


def test_a_retired_chore_or_routine_link_names_none() -> None:
    retired_chore = replace(_CHORE, retired=True)
    retired_routine = replace(_ROUTINE, retired=True)

    assert resolve_chain(ChainLinks(chore=retired_chore, routine=_ROUTINE), _DEFAULT) == _ROUTINE
    assert resolve_chain(ChainLinks(chore=retired_chore, routine=retired_routine), _DEFAULT) == _DEFAULT


def test_a_retired_stated_urgency_is_refused_rather_than_fallen_through() -> None:
    with pytest.raises(UrgencyRetired) as exc:
        resolve_chain(ChainLinks(stated=replace(_STATED, retired=True), chore=_CHORE), _DEFAULT)

    assert exc.value.field == URGENCY_FIELD
    assert exc.value.code == "urgency_retired"


def test_the_mint_locks_the_settings_the_default_and_each_named_urgency_once() -> None:
    ahead = MintAhead(default_id=_DEFAULT.urgency_id, stated=_DEFAULT, chore=_CHORE, routine=_CHORE)

    assert ahead.lock_keys() == [
        tenant_settings_lock(),
        urgency_lock(_DEFAULT.urgency_id),
        urgency_lock(_CHORE.urgency_id),
    ]
    assert MintAhead(default_id=_DEFAULT.urgency_id).lock_keys() == [
        tenant_settings_lock(),
        urgency_lock(_DEFAULT.urgency_id),
    ]
