"""The process environment the hub entrypoint borrows into its container."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class HubEnviron:
    """The environment variables the hub process was started with, read where a process setting is resolved."""

    values: Mapping[str, str]
