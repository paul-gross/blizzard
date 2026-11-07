"""The record of one declared problem variant."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from blizzard.foundation.roles import dto


@dto
@dataclass(frozen=True)
class ProblemDeclaration:
    """One operation/status/model variant, independent of route registration."""

    method: str
    path: str
    status_code: int
    model: type[BaseModel]
