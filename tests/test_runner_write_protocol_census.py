"""The write-protocol census exhaustiveness gate (D5, blizzard#317 Phase 3).

Introspects :class:`~blizzard.runner.store.repository.IWriteRunnerStore` at runtime for its
write-only members — declared on its own class body or inherited from a concept Protocol
such as :class:`~blizzard.runner.domain.leases.IWriteLeaseRepository` (blizzard#410), but
never a member also reachable through :class:`~blizzard.runner.store.repository.IReadRunnerStore`
— and asserts ``tests/runner_event_census.py`` names exactly that set — exhaustiveness by
test, not review."""

from __future__ import annotations

import pytest

from blizzard.runner.events.broker import EVENT_TYPES
from blizzard.runner.store.repository import IReadRunnerStore, IWriteRunnerStore
from tests.runner_event_census import WRITE_PROTOCOL_CENSUS, Published, Silent

pytestmark = pytest.mark.unit


def _reachable_members(protocol: type) -> set[str]:
    """The callable, non-dunder members ``protocol`` requires, own or inherited — a
    concept Protocol's members reached through composition count the same as one
    declared directly on ``protocol``'s own class body."""
    return {name for name in dir(protocol) if not name.startswith("_") and callable(getattr(protocol, name))}


def test_census_names_exactly_the_write_protocols_own_members() -> None:
    """The exhaustiveness gate: fails the instant ``IWriteRunnerStore`` grows (or loses) a
    write-only member this census does not account for."""
    write_only_members = _reachable_members(IWriteRunnerStore) - _reachable_members(IReadRunnerStore)
    assert set(WRITE_PROTOCOL_CENSUS) == write_only_members


def test_the_write_protocols_own_members_exclude_the_inherited_read_surface() -> None:
    """Sanity on the introspection itself: a read-only method reachable from
    ``IReadRunnerStore`` (e.g. ``list_active_leases``) must not appear as a write-only
    member — else the gate above would silently demand a disposition for the read surface
    too."""
    write_only_members = _reachable_members(IWriteRunnerStore) - _reachable_members(IReadRunnerStore)
    read_members = _reachable_members(IReadRunnerStore)
    assert "list_active_leases" in read_members
    assert "list_active_leases" not in write_only_members


def test_every_census_entry_is_a_published_or_silent_disposition() -> None:
    for name, disposition in WRITE_PROTOCOL_CENSUS.items():
        assert isinstance(disposition, Published | Silent), f"{name}: {disposition!r} is neither Published nor Silent"
        if isinstance(disposition, Published):
            assert disposition.where
            # A typo'd or stale kind (e.g. a deleted publish call's name left behind) is
            # otherwise merely truthy, not actually a kind the broker can publish.
            assert disposition.kind in EVENT_TYPES, f"{name}: {disposition.kind!r} is not a real event kind"
        else:
            assert disposition.reason
