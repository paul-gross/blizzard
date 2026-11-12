"""The environment variables a daemon reads its secret key from."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class KeyVariables:
    """The daemon's own names for the current key variable and the previous-generation one."""

    current: str
    previous: str
