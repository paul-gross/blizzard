"""The flag registry file, ``registry.toml``, and its loader.

Every flag is declared in that one data file, packaged with the wheel and read by the hub, every runner, and the
lifecycle check alike. The loader is strict: any malformed entry fails the whole load, so a bad declaration is found at
start rather than read as off."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal, Self

from blizzard.foundation.roles import domain_model

type FlagScope = Literal["hub", "runner", "fleet"]

_PACKAGED: Final = "registry.toml"
_NAME: Final = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_MAX_NAME: Final = 48
_OWNER: Final = re.compile(r"(epic|initiative):[a-z0-9]+(-[a-z0-9]+)*(/[a-z0-9]+(-[a-z0-9]+)*)*")
_FIELDS: Final = frozenset({"owner", "summary", "scope", "default"})
_SCOPES: Final[Mapping[str, FlagScope]] = {"hub": "hub", "runner": "runner", "fleet": "fleet"}
_DEFAULTS: Final = {"off": False, "on": True}


class FlagRegistryError(ValueError):
    """The registry file is missing, unparseable, or malformed; the message names the file."""


@domain_model
@dataclass(frozen=True)
class LiveFlag:
    """One declared flag: who owns it, what it does in the operator's words, whose answer counts, and its default."""

    name: str
    owner: str
    summary: str
    scope: FlagScope
    default: bool


@domain_model
@dataclass(frozen=True)
class FlagRegistry:
    """Every flag name the fleet knows: the live ones with their declarations, and the retired ones with their scope."""

    live: Mapping[str, LiveFlag] = field(default_factory=dict)
    retired: Mapping[str, FlagScope] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "live", MappingProxyType(dict(self.live)))
        object.__setattr__(self, "retired", MappingProxyType(dict(self.retired)))

    @classmethod
    def load(cls, source: Path | None = None) -> Self:
        """Parse ``source``, or the packaged ``registry.toml`` when none is given."""
        if source is None:
            source = Path(str(files("blizzard.foundation.flags").joinpath(_PACKAGED)))
        try:
            text = source.read_text()
        except OSError as error:
            raise FlagRegistryError(f"{source}: cannot read the flag registry: {error}") from error
        return cls.parse(text, str(source))

    @classmethod
    def parse(cls, text: str, source: str) -> Self:
        """Parse registry ``text`` read from ``source``, which labels every refusal — the same rule as ``load``."""
        try:
            document = tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            raise FlagRegistryError(f"{source}: not valid TOML: {error}") from error
        unknown = sorted(set(document) - {"flag", "retired"})
        if unknown:
            raise FlagRegistryError(f"{source}: unknown top-level key(s) {unknown}; expected [flag.*] and [retired]")
        live = {name: _live(name, body, source) for name, body in _table(document, "flag", source).items()}
        retired: dict[str, FlagScope] = {
            name: _tombstone(name, scope, source) for name, scope in _table(document, "retired", source).items()
        }
        for name in sorted(set(live) & set(retired)):
            raise FlagRegistryError(f"{source}: flag {name!r} is both live and retired; a name is never reused")
        return cls(live=live, retired=retired)

    def scope_of(self, name: str) -> FlagScope | None:
        """The scope of a live or retired ``name``; ``None`` when the registry holds neither."""
        if name in self.live:
            return self.live[name].scope
        return self.retired.get(name)


def _table(document: Mapping[str, Any], key: str, source: str) -> Mapping[str, Any]:
    value = document.get(key, {})
    if not isinstance(value, dict):
        raise FlagRegistryError(f"{source}: [{key}] must be a table")
    return value


def _checked_name(name: str, source: str) -> str:
    if len(name) > _MAX_NAME or _NAME.fullmatch(name) is None:
        raise FlagRegistryError(
            f"{source}: flag name {name!r} must be lowercase kebab-case of at most {_MAX_NAME} characters"
        )
    return name


def _live(name: str, body: object, source: str) -> LiveFlag:
    _checked_name(name, source)
    if not isinstance(body, dict):
        raise FlagRegistryError(f"{source}: [flag.{name}] must be a table")
    missing = sorted(_FIELDS - set(body))
    if missing:
        raise FlagRegistryError(f"{source}: [flag.{name}] is missing {missing}")
    extra = sorted(set(body) - _FIELDS)
    if extra:
        raise FlagRegistryError(f"{source}: [flag.{name}] has unknown key(s) {extra}")
    owner, summary, scope, default = body["owner"], body["summary"], body["scope"], body["default"]
    if not isinstance(owner, str) or _OWNER.fullmatch(owner) is None:
        raise FlagRegistryError(f"{source}: [flag.{name}] owner must be an `epic:` or `initiative:` id, got {owner!r}")
    if not isinstance(summary, str) or not summary.strip() or "\n" in summary:
        raise FlagRegistryError(f"{source}: [flag.{name}] summary must be one non-empty line")
    declared = _SCOPES.get(scope) if isinstance(scope, str) else None
    if declared is None:
        raise FlagRegistryError(f"{source}: [flag.{name}] scope must be one of {sorted(_SCOPES)}, got {scope!r}")
    if not isinstance(default, str) or default not in _DEFAULTS:
        raise FlagRegistryError(f'{source}: [flag.{name}] default must be "off" or "on", got {default!r}')
    return LiveFlag(name=name, owner=owner, summary=summary, scope=declared, default=_DEFAULTS[default])


def _tombstone(name: str, scope: object, source: str) -> FlagScope:
    _checked_name(name, source)
    declared = _SCOPES.get(scope) if isinstance(scope, str) else None
    if declared is None:
        raise FlagRegistryError(f"{source}: [retired] {name!r} scope must be one of {sorted(_SCOPES)}, got {scope!r}")
    return declared
