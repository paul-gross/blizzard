"""The configuration keys a daemon reads, declared once, and the strict accessor every read goes through.

A registry names each key of a daemon's toml file and each durable path under its runtime root; the loader passes it to
the root :class:`ConfigView`, so no section parser imports it. With ``BLIZZARD_STRICT_CONFIG=1`` — the suite sets it,
spawned daemons included — reading an undeclared key raises; unset, a read is a plain mapping
read and the key is ignored."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from blizzard.foundation.roles import domain_model

#: Set to ``1`` and an undeclared read raises; the test suite sets it for itself and every daemon it spawns.
STRICT_CONFIG_ENVIRONMENT_VARIABLE: Final = "BLIZZARD_STRICT_CONFIG"

#: The ``type`` of a table whose children are free-form: a read beneath it is always allowed.
OPEN_TABLE: Final = "open table"
TABLE: Final = "table"
#: The ``type`` of an array of tables; its children's paths mark it ``name[]``.
ARRAY_OF_TABLES: Final = "array of tables"
STRING: Final = "string"
INTEGER: Final = "integer"
NUMBER: Final = "number"
BOOLEAN: Final = "boolean"
STRING_LIST: Final = "list of strings"
#: A scalar written one of two ways, such as a lone URL or a list of them.
STRING_OR_LIST: Final = "string or list of strings"


class UndeclaredConfigKey(RuntimeError):
    """A key was read that its daemon's registry does not declare.

    Subclasses neither a daemon's ``ConfigError`` nor ``ValueError``/``TypeError``: a loader collects those into its
    refusal messages, and this is a defect in the code, not in the file."""

    def __init__(self, path: str, registry: str) -> None:
        self.path = path
        self.registry = registry
        super().__init__(f"config key {path!r} is read but not declared in the {registry} config registry")


@domain_model
@dataclass(frozen=True)
class ConfigKey:
    """One key of a daemon's toml file: its path (array tables marked ``name[]``), its type, whether the file must
    carry it, and its default — ``None`` when it has none, or when the daemon derives it at load."""

    path: str
    type: str
    required: bool = False
    default: object = None
    deprecated: bool = False
    #: The key or table that replaces a deprecated one.
    replaced_by: str | None = None


@domain_model
@dataclass(frozen=True)
class DurablePath:
    """A path under the daemon's runtime root that outlives a restart, and what it holds."""

    path: str
    holds: str


@domain_model
@dataclass(frozen=True)
class ConfigRegistry:
    """Everything one daemon reads from its toml file and keeps under its runtime root."""

    name: str
    keys: tuple[ConfigKey, ...]
    paths: tuple[DurablePath, ...] = ()
    _declared: frozenset[str] = field(init=False, repr=False, compare=False)
    _open: frozenset[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_declared", frozenset(key.path for key in self.keys))
        object.__setattr__(self, "_open", frozenset(key.path for key in self.keys if key.type == OPEN_TABLE))

    def declares(self, path: str) -> bool:
        """Whether ``path`` is a declared key, or lies beneath an open table."""
        return path in self._declared or self.is_open(path, strictly_beneath=True)

    def is_open(self, path: str, *, strictly_beneath: bool = False) -> bool:
        """Whether ``path`` is an open table, or — with ``strictly_beneath`` — lies beneath one."""
        if not strictly_beneath and path in self._open:
            return True
        parts = path.split(".")
        return any(".".join(parts[:end]) in self._open for end in range(1, len(parts)))

    def merged(self, name: str, keys: tuple[ConfigKey, ...], paths: tuple[DurablePath, ...] = ()) -> ConfigRegistry:
        """A registry of ``name`` holding these keys and paths after this one's."""
        return ConfigRegistry(name, (*self.keys, *keys), (*self.paths, *paths))


@domain_model
@dataclass(frozen=True)
class ConfigView:
    """A parsed toml table that remembers where it sits, so every read is checked against the registry.

    A value that is not a table reads as empty, so an absent section and an absent key behave alike."""

    registry: ConfigRegistry
    path: str
    body: Mapping[str, Any]
    #: ``False`` for a :meth:`blank` view, which no file fed and so has nothing to check.
    checked: bool = True

    @classmethod
    def root(cls, registry: ConfigRegistry, document: Mapping[str, Any]) -> ConfigView:
        return cls(registry, "", document)

    @classmethod
    def blank(cls) -> ConfigView:
        """An empty view standing for a table no file carries — a default built without reading one."""
        return cls.unchecked({})

    @classmethod
    def unchecked(cls, body: Mapping[str, Any]) -> ConfigView:
        """A view over a table built in code rather than read from a file, so no registry checks its reads."""
        return cls(ConfigRegistry("", ()), "", body, checked=False)

    def get(self, key: str, default: object = None) -> Any:
        """The value at ``key``, or ``default`` when the file does not carry it."""
        self._check(key)
        return self.body.get(key, default)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        self._check(key)
        return key in self.body

    def table(self, key: str) -> ConfigView:
        """The sub-table at ``key``; empty when absent or not a table."""
        value = self.get(key)
        return ConfigView(self.registry, self._join(key), value if isinstance(value, dict) else {}, self.checked)

    def element(self, key: str, value: object) -> ConfigView:
        """One entry of the array of tables at ``key``, its path marked ``key[]``; empty when not a table."""
        return ConfigView(self.registry, f"{self._join(key)}[]", value if isinstance(value, dict) else {}, self.checked)

    def entries(self, key: str) -> Iterator[ConfigView]:
        """Each table of the array at ``key``; none when absent or not an array."""
        value = self.get(key)
        if isinstance(value, list):
            yield from (self.element(key, entry) for entry in value)

    def items(self) -> Iterator[tuple[str, Any]]:
        """Every entry of an open table, whose keys no registry lists."""
        if self.checked and not self.registry.is_open(self.path):
            self._undeclared(f"{self.path}.*" if self.path else "*")
        return iter(self.body.items())

    def _join(self, key: str) -> str:
        return f"{self.path}.{key}" if self.path else key

    def _check(self, key: str) -> None:
        path = self._join(key)
        if self.checked and not self.registry.declares(path):
            self._undeclared(path)

    def _undeclared(self, path: str) -> None:
        if os.environ.get(STRICT_CONFIG_ENVIRONMENT_VARIABLE) == "1":
            raise UndeclaredConfigKey(path, self.registry.name)
