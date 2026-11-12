"""The urgency chain at every link, and the lock set a mint's chain names ahead of its unit, as pure values."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest

from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.kernel.transactions import Transaction
from blizzard.hub.domain.urgency.chain import URGENCY_FIELD, ChainLinks, required_urgency_reference, resolve_chain
from blizzard.hub.domain.urgency.model import Urgency, UrgencyId, UrgencyRetired
from blizzard.hub.domain.urgency.ports import (
    IReadTenantSettings,
    IReadUrgencyRepository,
    tenant_settings_lock,
    urgency_lock,
)
from blizzard.hub.domain.urgency.services import MintAhead, TenantDefault
from blizzard.hub.domain.urgency.settings import TenantSettings
from tests.support.urgency import urgency

_AT = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)


_DEFAULT = urgency("standard")
_STATED = urgency("stated")
_CHORE = urgency("chore")
_ROUTINE = urgency("routine")


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
    ahead = MintAhead(
        default_id=_DEFAULT.urgency_id,
        links=ChainLinks(stated=_DEFAULT.urgency_id, chore=_CHORE.urgency_id, routine=_CHORE.urgency_id),
    )

    assert ahead.lock_keys() == [
        tenant_settings_lock(),
        urgency_lock(_DEFAULT.urgency_id),
        urgency_lock(_CHORE.urgency_id),
    ]
    assert MintAhead(default_id=_DEFAULT.urgency_id).lock_keys() == [
        tenant_settings_lock(),
        urgency_lock(_DEFAULT.urgency_id),
    ]


class _Settings:
    def get(self, *, transaction: Transaction | None = None) -> TenantSettings:
        return TenantSettings("ten_a", _DEFAULT.urgency_id, 1, _AT, "usr_op")


class _Urgencies:
    """Each urgency by id; a read under a unit sees ``under_lock`` — the urgencies as a concurrent write left them."""

    def __init__(self, ahead: Sequence[Urgency], under_lock: Sequence[Urgency]) -> None:
        self._ahead = {u.urgency_id: u for u in ahead}
        self._under_lock = {u.urgency_id: u for u in under_lock}

    def get(self, urgency_id: UrgencyId, *, transaction: Transaction | None = None) -> Urgency | None:
        return (self._ahead if transaction is None else self._under_lock).get(urgency_id)


def test_a_link_retired_ahead_of_the_unit_is_still_locked_and_honored_once_enabled_under_the_lock() -> None:
    retired_chore = replace(_CHORE, retired=True)
    default = TenantDefault(
        settings=cast(IReadTenantSettings, _Settings()),
        urgencies=cast(IReadUrgencyRepository, _Urgencies([_DEFAULT, retired_chore], [_DEFAULT, _CHORE])),
    )

    ahead = default.ahead(chore=_CHORE.urgency_id)

    assert urgency_lock(_CHORE.urgency_id) in ahead.lock_keys()
    assert default.resolve(ahead, cast(Transaction, object())) == _CHORE


def test_a_chunk_urgency_reference_is_answered_as_named() -> None:
    assert required_urgency_reference("urgent") == "urgent"


def test_a_null_chunk_urgency_is_refused_on_the_urgency_field() -> None:
    with pytest.raises(ConfigFieldError) as refused:
        required_urgency_reference(None)

    assert (refused.value.field, refused.value.message) == (URGENCY_FIELD, "must not be null")
