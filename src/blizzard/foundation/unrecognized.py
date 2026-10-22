"""A response vocabulary value this build does not know, carried raw (bzh:open-response-vocabulary)."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class Unrecognized:
    """A wire value outside the vocabulary this build knows, as the server sent it.

    A response field typed with vocabulary ``E`` parses as ``E | Unrecognized``, so every typed consumer decides what
    an unknown value means to it; the value serializes back to ``raw`` unchanged."""

    raw: str

    def __str__(self) -> str:
        return self.raw
