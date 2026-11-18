"""The shared settings decisions, as pure functions over held values."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from blizzard.foundation.config_keys import INTEGER, ConfigKey, ConfigRegistry, ConfigView, Lifetime
from blizzard.foundation.settings_record.decision import Decision, decide, seed_choice
from blizzard.foundation.settings_record.refusals import SettingsRevisionConflict
from blizzard.foundation.settings_record.vocabulary import Change, Entry, FieldChange


def _decide(existing: Mapping[str, Entry], change: Change, *, if_match: int | None = 1) -> Decision:
    return decide(1, existing, change, if_match=if_match)


@pytest.mark.parametrize("if_match", [None, 7])
def test_a_missing_or_stale_if_match_is_refused(if_match: int | None) -> None:
    with pytest.raises(SettingsRevisionConflict) as refused:
        _decide({}, Change("edit", sets={"a": 1}), if_match=if_match)
    assert (refused.value.current, refused.value.presented) == (1, if_match)


def test_the_candidate_is_the_whole_overlay_of_the_change_on_the_existing_entries() -> None:
    decision = _decide({"a": Entry(1), "c": Entry(5)}, Change("edit", sets={"b": 2}, unsets=frozenset({"c"})))
    assert decision.candidate == {"a": Entry(1), "b": Entry(2), "c": Entry(None, at_default=True)}


def test_a_no_op_still_carries_the_complete_candidate() -> None:
    decision = _decide({"a": Entry(1)}, Change("edit", sets={"a": 1}))
    assert decision.diff == () and decision.candidate == {"a": Entry(1)}


def test_the_diff_is_only_the_moved_entries_in_order() -> None:
    decision = _decide({"a": Entry(1), "z": Entry(0)}, Change("edit", sets={"a": 2, "b": 3, "z": 0}))
    assert decision.diff == (FieldChange("a", Entry(1), Entry(2)), FieldChange("b", None, Entry(3)))


def test_an_unset_is_at_default_even_with_no_entry() -> None:
    decision = _decide({}, Change("edit", unsets=frozenset({"a"})))
    assert decision.diff == (FieldChange("a", None, Entry(None, at_default=True)),)


def test_unsetting_an_at_default_entry_moves_nothing() -> None:
    assert _decide({"a": Entry(None, at_default=True)}, Change("edit", unsets=frozenset({"a"}))).diff == ()


def test_absent_at_default_and_explicit_null_are_distinct_states() -> None:
    at_default = Entry(None, at_default=True)
    explicit_null = Entry(None)
    assert _decide({"a": at_default}, Change("edit", sets={"a": None})).diff == (
        FieldChange("a", at_default, explicit_null),
    )
    assert _decide({}, Change("edit", sets={"a": None})).diff == (FieldChange("a", None, explicit_null),)


def test_a_key_both_set_and_unset_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="both set and unset"):
        Change("edit", sets={"a": 1}, unsets=frozenset({"a"}))


_REGISTRY = ConfigRegistry(
    "demo",
    (
        ConfigKey("a", INTEGER, lifetime=Lifetime.BOOT),
        ConfigKey("b", INTEGER, lifetime=Lifetime.BOOT),
        ConfigKey("c", INTEGER, lifetime=Lifetime.BOOT),
    ),
)


def _file(document: Mapping[str, object]) -> ConfigView:
    return ConfigView.root(_REGISTRY, document, strict=True)


@pytest.mark.parametrize("existing", [Entry(5), Entry(None, at_default=True)])
def test_seed_skips_an_existing_entry_tombstone_included(existing: Entry) -> None:
    assert seed_choice({"a": existing}, ("a",), _file({"a": 9})) == {}


def test_seed_skips_a_key_the_file_does_not_state() -> None:
    assert seed_choice({}, ("a",), _file({"b": 1})) == {}


def test_seed_returns_stated_unseeded_keys_in_order_with_their_authored_values() -> None:
    chosen = seed_choice({"b": Entry(1)}, ("c", "b", "a"), _file({"a": 9, "b": 2, "c": 4}))
    assert list(chosen.items()) == [("c", 4), ("a", 9)]
