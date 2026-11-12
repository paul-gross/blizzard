"""The two ways a settings write is refused. Neither carries a transport status; a route maps them."""

from __future__ import annotations

from collections.abc import Mapping


class SettingsRevisionConflict(Exception):
    """The presented revision is not the current one, or none was presented."""

    def __init__(self, current: int, presented: int | None) -> None:
        self.current = current
        self.presented = presented
        super().__init__(f"settings revision is {current}, presented {presented}")


class SettingsInvalid(Exception):
    """The resolved settings fail validation; ``errors`` maps each failing key to its message."""

    def __init__(self, errors: Mapping[str, str]) -> None:
        self.errors = dict(errors)
        super().__init__("; ".join(f"{key}: {message}" for key, message in self.errors.items()))
