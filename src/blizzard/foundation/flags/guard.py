"""What a flag hides: the marker on a wire-model field a flag guards (``bzh:flag-hides-behavior``).

The guard of a route, a command or an option lives with its surface; this module holds the field marker and the
function that applies it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from blizzard.foundation.flags.flags import Flags
from blizzard.foundation.roles import domain_model
from blizzard.foundation.untyped import as_list


@domain_model
@dataclass(frozen=True)
class FlagGuarded:
    """Metadata marking a wire-model field a flag guards: ``Annotated[int, FlagGuarded("name")]``.

    While the flag is off the field reads as its default on a request and on a response, whatever was sent or held."""

    flag: str


def without_off_fields[Model: BaseModel](model: Model, flags: Flags) -> Model:
    """``model`` with every field whose :class:`FlagGuarded` flag is off reset to its default, nested models and lists
    of models included. A guarded field must declare a default: a required one cannot read as absent."""
    updates: dict[str, Any] = {}
    for name, info in type(model).model_fields.items():
        guards = [m for m in info.metadata if isinstance(m, FlagGuarded)]
        if guards and info.is_required():
            raise TypeError(f"{type(model).__name__}.{name} is flag-guarded and so needs a default")
        if any(not flags.on(g.flag) for g in guards):
            updates[name] = info.get_default(call_default_factory=True, validated_data={})
            continue
        held = getattr(model, name)
        if (shed := _shed(held, flags)) is not held:
            updates[name] = shed
    return model.model_copy(update=updates) if updates else model


def _shed(value: Any, flags: Flags) -> Any:
    if isinstance(value, BaseModel):
        return without_off_fields(value, flags)
    items = as_list(value)
    if items is not None:
        shed = [_shed(item, flags) for item in items]
        return shed if any(a is not b for a, b in zip(shed, items, strict=True)) else value
    return value
