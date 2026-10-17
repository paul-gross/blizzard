"""Validation of an ordered harness preference list shared by hub authoring paths."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.roles import domain_model


class InvalidHarnesses(ValueError):
    """A preference contains a blank or a duplicate after whitespace normalization."""

    def __init__(self, reason: str, entry: str = "") -> None:
        self.reason = reason
        self.entry = entry
        super().__init__(f"default_harnesses entries must {'not be blank' if reason == 'blank' else 'be unique'}")


@domain_model
@dataclass(frozen=True)
class HarnessProblem:
    """One entry of a harness preference that is blank (``reason == "blank"``) or repeats an earlier one
    (``"duplicate"``), at its 1-based ``position``; ``entry`` is the stripped text, empty for a blank."""

    position: int
    reason: str
    entry: str


def harness_problems(entries: list[str]) -> list[HarnessProblem]:
    """Every blank and duplicate entry of ``entries``, in position order, after whitespace normalization."""
    problems: list[HarnessProblem] = []
    seen: set[str] = set()
    for position, entry in enumerate(entries, start=1):
        stripped = entry.strip()
        if not stripped:
            problems.append(HarnessProblem(position, "blank", ""))
        elif stripped in seen:
            problems.append(HarnessProblem(position, "duplicate", stripped))
        seen.add(stripped)
    return problems


def validated_harnesses(entries: list[str]) -> list[str]:
    """Strip entries, retain priority order, and allow an empty no-preference list. Raises for the first
    blank entry, else the first duplicate."""
    problems = harness_problems(entries)
    first = next((p for p in problems if p.reason == "blank"), problems[0] if problems else None)
    if first is not None:
        raise InvalidHarnesses(first.reason, first.entry)
    return [entry.strip() for entry in entries]
