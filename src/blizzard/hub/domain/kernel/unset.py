from __future__ import annotations

from enum import Enum
from typing import Final


class UnsetType(Enum):
    """The type of :data:`UNSET` — a single-member enum, not a plain class, so
    ``is``/``is not`` comparisons against it narrow a ``T | UnsetType`` union for
    pyright (identity narrowing on a bare class instance is not reliably supported;
    on an enum literal it is)."""

    TOKEN = 0


#: "Field absent from the request, leave it unchanged" — distinct from ``None``, which
#: means "clear it", and from a field's own falsy value.
UNSET: Final = UnsetType.TOKEN
