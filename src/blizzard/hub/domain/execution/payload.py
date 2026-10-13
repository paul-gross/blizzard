"""One pushed fact's body, read through the coercions both fact intakes share."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.execution.questions import parse_instant


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
        return int(self.body[key])  # type: ignore[arg-type]

    def amount(self, key: str) -> float | None:
        """A usage fact's dollar figure by ``key`` — ``cost_usd`` or ``estimated_cost_usd`` — ``None``
        stays ``None`` (no such figure was reported), never fabricated."""
        value = self.body.get(key)
        return float(value) if value is not None else None  # type: ignore[arg-type]

    def strings(self, key: str) -> list[str]:
        return [str(item) for item in self.body.get(key, [])]  # type: ignore[union-attr]

    def mapping(self, key: str) -> dict[str, object] | None:
        value = self.body.get(key)
        return value if isinstance(value, dict) else None

    def instant(self, key: str, fallback: datetime) -> datetime:
        """An ISO-8601 stamp, falling back on a malformed one and coerced to UTC
        (``bzh:utc-instants``) — store-and-forward resends the naive stamp it buffered."""
        return parse_instant(self.body.get(key), fallback)
