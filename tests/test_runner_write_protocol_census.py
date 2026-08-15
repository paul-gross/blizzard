"""The write-protocol census exhaustiveness gate (D5, blizzard#317 Phase 3).

Introspects :class:`~blizzard.runner.store.repository.IWriteRunnerStore` at runtime for the
members it declares **itself**, never what it inherits, and asserts
:mod:`blizzard.runner.events.census` names exactly that set — exhaustiveness by test, not review."""

from __future__ import annotations

import pytest

from blizzard.runner.events.census import WRITE_PROTOCOL_CENSUS, Published, Silent
from blizzard.runner.store.repository import IReadRunnerStore, IWriteRunnerStore

pytestmark = pytest.mark.unit


def _own_declared_members(protocol: type) -> set[str]:
    """The callable, non-dunder members ``protocol``'s own class body declares — excludes
    everything inherited from a base, which is what makes this "own", not "declares or
    inherits"."""
    return {name for name, value in vars(protocol).items() if callable(value) and not name.startswith("_")}


def test_census_names_exactly_the_write_protocols_own_members() -> None:
    """The exhaustiveness gate: fails the instant ``IWriteRunnerStore`` grows (or loses) a
    member this census does not account for."""
    own_write_members = _own_declared_members(IWriteRunnerStore)
    assert set(WRITE_PROTOCOL_CENSUS) == own_write_members


def test_the_write_protocols_own_members_exclude_the_inherited_read_surface() -> None:
    """Sanity on the introspection itself: a read-only method inherited from
    ``IReadRunnerStore`` (e.g. ``list_active_leases``) must not appear as a write member —
    else the gate above would silently demand a disposition for the read surface too."""
    own_write_members = _own_declared_members(IWriteRunnerStore)
    own_read_members = _own_declared_members(IReadRunnerStore)
    assert own_read_members & own_write_members == set()
    assert "list_active_leases" in own_read_members
    assert "list_active_leases" not in own_write_members


def test_every_census_entry_is_a_published_or_silent_disposition() -> None:
    for name, disposition in WRITE_PROTOCOL_CENSUS.items():
        assert isinstance(disposition, Published | Silent), f"{name}: {disposition!r} is neither Published nor Silent"
        if isinstance(disposition, Published):
            assert disposition.kind and disposition.where
        else:
            assert disposition.reason
