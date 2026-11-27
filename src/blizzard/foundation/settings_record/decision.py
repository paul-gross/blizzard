"""Pure decisions for one settings-record write: the revision check, the overlay, the diff and the seed plan."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from blizzard.foundation.config_keys import ConfigView
from blizzard.foundation.roles import domain_model
from blizzard.foundation.settings_record.refusals import SettingsRevisionConflict
from blizzard.foundation.settings_record.vocabulary import Change, Entry, FieldChange


@domain_model
@dataclass(frozen=True)
class Decision:
    """What a change would produce: the complete candidate entries and the ordered entries that move."""

    candidate: Mapping[str, Entry]
    diff: tuple[FieldChange, ...]


def decide(current: int, existing: Mapping[str, Entry], change: Change, *, if_match: int | None) -> Decision:
    """Refuse a missing or stale revision, then overlay the change on the held entries.

    The caller validates ``candidate`` before it mutates anything, even when ``diff`` is empty."""
    if if_match is None or if_match != current:
        raise SettingsRevisionConflict(current, if_match)
    candidate = {**existing, **{key: Entry(value) for key, value in change.sets.items()}}
    candidate.update({key: Entry(None, at_default=True) for key in change.unsets})
    diff = tuple(
        FieldChange(key, existing.get(key), entry) for key, entry in candidate.items() if existing.get(key) != entry
    )
    return Decision(candidate, diff)


def seed_choice(existing: Mapping[str, Entry], keys: Sequence[str], file: ConfigView) -> dict[str, object]:
    """The keys to seed, in order, with their whole authored values.

    An existing entry, tombstone included, is skipped, as is a key the file does not state."""
    return {key: file.value(key) for key in keys if key not in existing and file.states(key)}


@domain_model
@dataclass(frozen=True)
class SeedPlan:
    """What a whole seed would take from the file: the keys still open to it, and every entry it would make."""

    keys: tuple[str, ...]
    pending: Mapping[str, Entry]


def plan_seed(
    existing: Mapping[str, Entry], keys: Sequence[str], file: ConfigView, aliases: Mapping[str, str] | None = None
) -> SeedPlan:
    """Plan the whole seed of ``keys``; ``aliases`` maps a legacy spelling to its replacement.

    An alias and its replacement are one authored setting: once either has an entry, neither spelling is seeded."""
    if aliases:
        blocked = {alias for alias, key in aliases.items() if key in existing}
        blocked.update(key for alias, key in aliases.items() if alias in existing)
        keys = [key for key in keys if key not in blocked]
    return SeedPlan(tuple(keys), {key: Entry(value) for key, value in seed_choice(existing, keys, file).items()})
