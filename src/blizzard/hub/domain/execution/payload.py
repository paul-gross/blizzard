"""One pushed fact's body, read through the coercions both fact intakes share."""

from __future__ import annotations

from collections.abc import Buffer, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import SupportsFloat, SupportsIndex, SupportsInt

from typing_extensions import TypeIs

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.execution.questions import parse_instant


def _string_keyed_mapping(value: object) -> TypeIs[dict[str, object]]:
    return _iterable(value) and isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _iterable(value: object) -> TypeIs[Iterable[object]]:
    return isinstance(value, Iterable)


@domain_model
@dataclass(frozen=True)
class Payload:
    """One pushed fact's body, read through the coercions the intake shares.

    An absent key and an explicit ``null`` are distinct: :meth:`text` reads both as
    ``None``, while :meth:`string`'s default only covers the absent one."""

    body: dict[str, object]

    def get(self, key: str, default: object = None) -> object:
        return self.body.get(key, default)

    def text(self, key: str) -> str | None:
        value = self.body.get(key)
        return str(value) if value is not None else None

    def string(self, key: str, default: str = "") -> str:
        return str(self.body.get(key, default))

    def require_text(self, key: str) -> str:
        return str(self.body[key])

    def require_number(self, key: str) -> int:
        value = self.body[key]
        if isinstance(value, (str, Buffer, SupportsInt, SupportsIndex)):
            return int(value)
        raise TypeError(
            f"int() argument must be a string, a bytes-like object or a real number, not '{type(value).__name__}'"
        )

    def amount(self, key: str) -> float | None:
        """A usage fact's dollar figure by ``key`` — ``cost_usd`` or ``estimated_cost_usd`` — ``None``
        stays ``None`` (no such figure was reported), never fabricated."""
        value = self.body.get(key)
        if value is None:
            return None
        if isinstance(value, (str, Buffer, SupportsFloat, SupportsIndex)):
            return float(value)
        raise TypeError(f"float() argument must be a string or a real number, not '{type(value).__name__}'")

    def strings(self, key: str) -> list[str]:
        value = self.body.get(key, [])
        if not _iterable(value):
            raise TypeError(f"'{type(value).__name__}' object is not iterable")
        return [str(item) for item in value]

    def mapping(self, key: str) -> dict[str, object] | None:
        value = self.body.get(key)
        if not _string_keyed_mapping(value):
            return None
        return value

    def instant(self, key: str, fallback: datetime) -> datetime:
        """:func:`~blizzard.hub.domain.execution.questions.parse_instant` over ``key``'s value."""
        return parse_instant(self.body.get(key), fallback)
