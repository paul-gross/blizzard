"""The finding every surface classifier reports."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class Violation:
    """One breaking-change finding on a surface, and whether the baseline marked the thing deprecated.

    ``surface`` uses the change-note vocabulary: ``wire``, ``http-api``, ``config``,
    ``trace``, ``egress``, ``store``."""

    surface: str
    where: str
    reason: str
    deprecated: bool = False

    def __str__(self) -> str:
        return f"{self.where}: {self.reason}"
