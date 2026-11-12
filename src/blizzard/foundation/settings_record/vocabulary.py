"""The settings record's shared vocabulary: how a change is named, where it came in, and what it moved."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from blizzard.foundation.roles import domain_model

type Op = Literal["seed", "edit", "create", "retire", "enable"]
type Door = Literal["seed", "cli", "page", "api", "apply"]

#: The door and actor a seed write is recorded under.
SEED: Final = "seed"


@domain_model
@dataclass(frozen=True)
class Entry:
    """A stored entry: its whole value, and whether it stands at the default rather than being set."""

    value: object
    at_default: bool = False


@domain_model
@dataclass(frozen=True)
class Change:
    """What a write asks for: the keys it sets to a whole value and the keys it unsets.

    A key both set and unset is a contradiction and is refused at construction."""

    op: Op
    sets: Mapping[str, object] = field(default_factory=dict[str, object])
    unsets: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        both = sorted(self.unsets & self.sets.keys())
        if both:
            raise ValueError(f"a change cannot both set and unset {both}")


@domain_model
@dataclass(frozen=True)
class FieldChange:
    """One moved key with its old and new state; ``None`` is absent, so absent, at-default and a value differ."""

    key: str
    old: Entry | None
    new: Entry
