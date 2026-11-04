"""The host's identity at its hub: the store's single ``host_identity`` row, written once and never edited, and the
process holder every consumer reads."""

from __future__ import annotations

from blizzard.runner.hub.host_identity import HostIdentityHolder
from tests.support_host_identity import (
    _IDENTITY,  # pyright: ignore[reportPrivateUsage]
)


def test_the_holder_answers_its_seed_then_each_identity_it_is_handed() -> None:
    holder = HostIdentityHolder()
    assert holder.current() is None
    holder.hold(_IDENTITY)
    assert holder.current() == _IDENTITY
    assert HostIdentityHolder(_IDENTITY).current() == _IDENTITY


def test_an_identity_names_the_same_host_only_by_id_and_tenant_together() -> None:
    assert _IDENTITY.same_host("hs_1", "tn_1")
    assert not _IDENTITY.same_host("hs_1", "tn_2")
    assert not _IDENTITY.same_host("hs_2", "tn_1")
