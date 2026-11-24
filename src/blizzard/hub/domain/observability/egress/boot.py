"""The fact-egress export as the hub booted it, and the roles its services are wired over.

The export is decided once, at boot: off with no directory, rejected when its format's writer is unavailable, or on.
That decision, the boot config and the path hash key travel as one :class:`EgressBoot`; the live config, the writers
and the free-space probe each reach the services as their own role type."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.domain.observability.egress.event_rows import ExportPaths, export_paths
from blizzard.hub.domain.observability.egress.ports import IEgressWriter

__all__ = [
    "EgressBoot",
    "EgressPathKey",
    "IConfiguredEgressWriter",
    "IEgressBackfillWriters",
    "IEgressFreeSpace",
    "LiveEgressConfig",
]


@domain_model
@dataclass(frozen=True)
class EgressPathKey:
    """The file path hash key the variable ``egress.path_key_env`` names, read once at boot; ``None`` when unset."""

    value: bytes | None = None


@domain_model
@dataclass(frozen=True)
class EgressBoot:
    """The export as the hub booted it over ``config``: off with no directory, rejected for ``rejected``'s reason
    when its format's writer is unavailable, or on."""

    config: EgressConfig
    path_key: EgressPathKey
    #: Why the configured export could not start; ``None`` when it could, or none is set.
    rejected: str | None = None

    @classmethod
    def off(cls) -> EgressBoot:
        """The export off: no directory and no key."""
        return cls(EgressConfig(), EgressPathKey())

    def on(self) -> bool:
        """Whether the export runs: a directory is set and its format's writer is available."""
        return self.config.directory is not None and self.rejected is None

    @property
    def exported(self) -> ExportPaths:
        """The boot config narrowed to the datasets the key lets it write, and its file path policy."""
        return export_paths(self.config, self.path_key.value)

    def missing_path_key(self) -> str | None:
        """The unset ``path_key_env`` variable that dropped the ``events`` dataset from a running export; else
        ``None`` — with the export off, nothing would have been written anyway."""
        return self.exported.missing_key_env if self.on() else None

    def live(self, config: LiveEgressConfig) -> Callable[[], ExportPaths]:
        """The live config's export paths under this boot's key."""
        return lambda: export_paths(config.current(), self.path_key.value)


class LiveEgressConfig:
    """The ``[egress]`` config the running pass bound, else the live resolution."""

    def __init__(self, read: Callable[[], EgressConfig]) -> None:
        self._read = read

    def current(self) -> EgressConfig:
        return self._read()


class IConfiguredEgressWriter(Protocol):
    """The running export's writer, kept until its file-size or free-space settings change."""

    def for_config(self, config: EgressConfig) -> IEgressWriter:
        """The writer for ``config``'s settings, rebuilt only when they changed."""
        ...


class IEgressBackfillWriters(Protocol):
    """Mints a fresh writer for each backfill."""

    def __call__(self, config: EgressConfig) -> IEgressWriter:
        """A new writer over ``config``'s format, directory and settings."""
        ...


class IEgressFreeSpace(Protocol):
    """The export directory's free space."""

    def __call__(self) -> int | None:
        """Bytes free, or ``None`` with no directory or when it cannot be read."""
        ...
