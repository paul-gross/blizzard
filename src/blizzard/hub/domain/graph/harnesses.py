"""Validation of an ordered harness preference list shared by hub authoring paths."""

from __future__ import annotations


class InvalidHarnesses(ValueError):
    """A preference contains a blank or a duplicate after whitespace normalization."""

    def __init__(self, reason: str, entry: str = "") -> None:
        self.reason = reason
        self.entry = entry
        super().__init__(f"default_harnesses entries must {'not be blank' if reason == 'blank' else 'be unique'}")


def validated_harnesses(entries: list[str]) -> list[str]:
    """Strip entries, retain priority order, and allow an empty no-preference list."""
    stripped = [entry.strip() for entry in entries]
    if any(not entry for entry in stripped):
        raise InvalidHarnesses("blank")
    seen: set[str] = set()
    for entry in stripped:
        if entry in seen:
            raise InvalidHarnesses("duplicate", entry)
        seen.add(entry)
    return stripped
