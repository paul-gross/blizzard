"""The configuration keys a daemon reads, declared once, and the strict accessor every read goes through.

A registry names each key of a daemon's toml file and each durable path under its runtime root; the loader passes it to
the root :class:`ConfigView`, so no section parser imports it. The loader also says whether the view is strict: a strict
view raises on reading an undeclared key; a lenient one reads it as a plain mapping read and the key is ignored."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Final

from blizzard.foundation.roles import domain_model

if TYPE_CHECKING:
    from blizzard.foundation.flags import Flags

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


class InvalidConfigValue(ValueError):
    """A guarded key carries a value of the wrong type for its declaration; the message names the key."""


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
    #: The flag holding this key: off, it reads as absent once its value passed ``type``; not in the contract.
    flag: str | None = None


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
    _by_path: Mapping[str, ConfigKey] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_path", {key.path: key for key in self.keys})
        object.__setattr__(self, "_declared", frozenset(key.path for key in self.keys))
        object.__setattr__(self, "_open", frozenset(key.path for key in self.keys if key.type == OPEN_TABLE))

    def key(self, path: str) -> ConfigKey | None:
        """The declaration of ``path``, or ``None`` when it has none."""
        return self._by_path.get(path)

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


SCALAR_TYPES: Final = (STRING, INTEGER, NUMBER, BOOLEAN, STRING_LIST, STRING_OR_LIST)

_TYPE_CHECKS: Final[Mapping[str, Callable[[object], bool]]] = {
    STRING: lambda v: isinstance(v, str),
    INTEGER: lambda v: isinstance(v, int) and not isinstance(v, bool),
    NUMBER: lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    BOOLEAN: lambda v: isinstance(v, bool),
    STRING_LIST: lambda v: isinstance(v, list) and all(isinstance(item, str) for item in v),
    STRING_OR_LIST: lambda v: isinstance(v, str) or (isinstance(v, list) and all(isinstance(i, str) for i in v)),
}


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
    #: Whether reading an undeclared key raises, rather than reading it plainly; set at the root.
    strict: bool = False
    #: The flags guarded keys read against, set by a loader once it has resolved them; a view without them reads every
    #: key plainly.
    flags: Flags | None = None

    @classmethod
    def root(cls, registry: ConfigRegistry, document: Mapping[str, Any], *, strict: bool) -> ConfigView:
        return cls(registry, "", document, strict=strict)

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
        return default if self._held(key) else self.body.get(key, default)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        self._check(key)
        return key in self.body and not self._held(key)

    def table(self, key: str) -> ConfigView:
        """The sub-table at ``key``; empty when absent or not a table."""
        value = self.get(key)
        return replace(self, path=self._join(key), body=value if isinstance(value, dict) else {})

    def element(self, key: str, value: object) -> ConfigView:
        """One entry of the array of tables at ``key``, its path marked ``key[]``; empty when not a table."""
        return replace(self, path=f"{self._join(key)}[]", body=value if isinstance(value, dict) else {})

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

    def holding(self, flags: Flags) -> ConfigView:
        """This view with its guarded keys read against ``flags``."""
        return replace(self, flags=flags)

    def _held(self, key: str) -> bool:
        """Whether ``key`` is present, guarded, and its flag off — after its value passed the declared type, so a
        malformed value fails whatever the flag says."""
        declared = self.registry.key(self._join(key)) if self.checked and self.flags is not None else None
        if declared is None or declared.flag is None or key not in self.body:
            return False
        assert self.flags is not None
        if not _TYPE_CHECKS[declared.type](self.body[key]):
            raise InvalidConfigValue(f"{declared.path} must be a {declared.type}, got {self.body[key]!r}")
        return not self.flags.on(declared.flag)

    def _join(self, key: str) -> str:
        return f"{self.path}.{key}" if self.path else key

    def _check(self, key: str) -> None:
        path = self._join(key)
        if self.checked and not self.registry.declares(path):
            self._undeclared(path)

    def _undeclared(self, path: str) -> None:
        if self.strict:
            raise UndeclaredConfigKey(path, self.registry.name)
