"""The TOML reading primitives the runner config and every harness section share.

Kept apart from :mod:`blizzard.runner.config` so a harness's own config section can parse its
table without importing the config module that holds the parsed sections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ConfigError(RuntimeError):
    """A runtime directory is missing its config — it was never initialized."""


@dataclass(frozen=True)
class Table:
    """One parsed toml table, read through the coercions the config fields share.

    A value that is not a table reads as empty, so an absent section and an absent key
    behave alike."""

    body: dict[str, Any]

    @classmethod
    def of(cls, value: object) -> Table:
        return cls(value if isinstance(value, dict) else {})

    def text(self, key: str) -> str | None:
        value = self.body.get(key)
        return None if value is None else str(value)

    def word(self, key: str) -> str | None:
        """A string read whose empty value counts as absent."""
        value = self.body.get(key)
        return str(value) if value else None

    def real(self, key: str) -> float | None:
        value = self.body.get(key)
        return None if value is None else float(value)

    def count(self, key: str, default: int) -> int:
        value = self.body.get(key)
        return default if value is None else int(value)

    def boolean(self, key: str, default: bool) -> bool:
        """A real TOML boolean, or ``default`` when ``key`` is absent. Raises on anything
        else: ``bool()`` on a non-empty string is truthy regardless of its
        text, so a typo'd ``ship = "false"`` must never silently turn a switch on."""
        value = self.body.get(key)
        if value is None:
            return default
        if not isinstance(value, bool):
            raise ConfigError(f"{key!r} must be a boolean, got {value!r}")
        return value

    def names(self, key: str) -> tuple[str, ...]:
        """Every entry at ``key`` as a string; an absent key is empty."""
        value = self.body.get(key)
        return () if value is None else tuple(str(entry) for entry in value)

    def listed(self, key: str, default: tuple[str, ...]) -> tuple[str, ...]:
        """Every entry at ``key`` as a string, falling back to ``default`` unless it is a list."""
        value = self.body.get(key)
        if not isinstance(value, (list, tuple)):
            return default
        return tuple(str(entry) for entry in value)

    def pairs(self, key: str) -> tuple[tuple[str, str], ...]:
        """The nested table at ``key`` as key/value pairs — a frozen dataclass field must
        stay hashable. Absent, or present but empty, means none."""
        nested = self.body.get(key)
        if not isinstance(nested, dict):
            return ()
        return tuple((str(name), str(value)) for name, value in nested.items())
