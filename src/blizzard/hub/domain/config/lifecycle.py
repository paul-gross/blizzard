"""Pure mechanics shared by configured records, with meaning supplied by each kind."""

from collections.abc import Callable, Iterable

from blizzard.hub.domain.config.changes import (
    FIELDED_RECORD_TRANSITIONS,
    ChangeOp,
    FieldChange,
    RecordState,
    Verdict,
)
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.kernel.slugs import DisplayName, Slug
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType


def require_revision(if_match: int | None, revision: int, *, kind: str, key: str) -> None:
    """Refuse a stale revision, including before an otherwise redundant write."""
    if if_match is not None and if_match != revision:
        raise ConfigRevisionConflict(kind, key, current=revision)


def transition(retired: bool, desired: bool) -> ChangeOp | None:
    """The fielded-record lifecycle operation, or none when already in the desired state."""
    op = ChangeOp.RETIRE if desired else ChangeOp.ENABLE
    return None if FIELDED_RECORD_TRANSITIONS[RecordState.of(retired)][op] is Verdict.NO_OP else op


def field_changes(fields: Iterable[tuple[str, object, object]]) -> tuple[FieldChange, ...]:
    """Compare the caller's ordered, already projected field values."""
    return tuple(FieldChange(name, old, new) for name, old, new in fields if old != new)


def slug_left_for(current: str, edited: str) -> str | None:
    return current if current != edited else None


def merge_labels(
    slug: str,
    name: str,
    new_slug: str | None | UnsetType,
    new_name: str | None | UnsetType,
    *,
    parse_slug: Callable[[str], Slug],
    parse_name: Callable[[str], DisplayName],
) -> tuple[str, str]:
    """Merge and validate supplied labels, refusing explicit null even on an unchanged edit."""

    def merged(field: str, held: str, value: str | None | UnsetType, parse: Callable[[str], Slug | DisplayName]) -> str:
        if value is UNSET:
            return held
        if value is None:
            raise ConfigFieldError(field, "must not be null")
        return parse(value).value

    return merged("slug", slug, new_slug, parse_slug), merged("name", name, new_name, parse_name)
