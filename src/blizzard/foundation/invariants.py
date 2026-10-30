"""The invariant checker's shared vocabulary: a breach and the shapes of check that find one."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection

from blizzard.foundation.roles import collaborator, domain_model


@domain_model
@dataclass(frozen=True)
class Violation:
    invariant: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.invariant}] {self.detail}"


@collaborator
@dataclass(frozen=True)
class Check:
    """One durable invariant, asserted against a store — every breach it finds, in the
    order it found them."""

    def run(self) -> list[Violation]:
        raise NotImplementedError


@dataclass(frozen=True)
class QueryCheck(Check):
    """An invariant answerable by plain queries over one open read connection."""

    conn: Connection
