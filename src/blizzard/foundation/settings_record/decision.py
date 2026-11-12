"""Pure decisions for one settings-record write."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from blizzard.foundation.settings_record.refusals import SettingsInvalid, SettingsRevisionConflict
from blizzard.foundation.settings_record.vocabulary import Change, Entry, FieldChange

type Validate = Callable[[Mapping[str, Entry]], Mapping[str, str]]


def decide(
    current: int,
    existing: Mapping[str, Entry],
    change: Change,
    *,
    if_match: int | None,
    validate: Validate,
) -> tuple[FieldChange, ...]:
    """Refuse stale or invalid changes, then return exactly the entries that move."""
    if if_match is None or if_match != current:
        raise SettingsRevisionConflict(current, if_match)
    resolved = {**existing, **{key: Entry(value) for key, value in change.sets.items()}}
    resolved.update({key: Entry(None, at_default=True) for key in change.unsets})
    errors = validate(resolved)
    if errors:
        raise SettingsInvalid(errors)
    return tuple(
        FieldChange(key, existing.get(key), entry) for key, entry in resolved.items() if existing.get(key) != entry
    )
