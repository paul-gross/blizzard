"""The egress adapter package's registrations: the free-space probe over the boot export directory, and — only when
the export is on — the running export's writer, the writer it keeps per settings, and the backfill's writer factory.

Each is one instance per process. The writers hold no finalizer: each opens and closes a file per write. The
observability area aliases its writer roles onto the ``Ref`` handed back, so it names no adapter here."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.foundation.roles import adapter_model
from blizzard.hub.domain.observability.egress.boot import EgressBoot
from blizzard.hub.domain.observability.egress.ports import IEgressWriter
from blizzard.hub.egress.factory import BackfillWriters, ConfiguredWriter, running_writer, writer_settings
from blizzard.hub.egress.space import DirectoryFreeSpace
from blizzard.hub.tenancy import STORE_UNAVAILABLE

#: Why a writer is absent: the export is off, or the graph holds no store.
EXPORT_OFF = "the fact-egress export is off"


def free_space(boot: EgressBoot) -> DirectoryFreeSpace:
    """The probe over the boot export directory."""
    return DirectoryFreeSpace(boot.config.directory)


def export_writer(boot: EgressBoot) -> IEgressWriter:
    """The running export's writer, over the boot config's format, directory and settings."""
    return running_writer(boot.config, writer_settings(boot.config))


def configured_writer(writer: IEgressWriter, boot: EgressBoot) -> ConfiguredWriter:
    """The export's writer, rebuilt over the boot format and directory whenever the live settings change."""
    return ConfiguredWriter(
        writer, writer_settings(boot.config), lambda settings: running_writer(boot.config, settings)
    )


def backfill_writers() -> BackfillWriters:
    """The backfill's writer factory."""
    return BackfillWriters()


@adapter_model
@dataclass(frozen=True)
class EgressWriterRefs:
    """The ``Ref`` of each writer the running export holds."""

    configured: Ref[ConfiguredWriter]
    backfill: Ref[BackfillWriters]


#: The writer keys, absent while the export is off.
_WRITER_KEYS: tuple[type[Any], ...] = (IEgressWriter, ConfiguredWriter, BackfillWriters)


def register_probe(registry: Registry) -> Ref[DirectoryFreeSpace]:
    """Declare the free-space probe, handing back its ``Ref``."""
    return registry.factory(free_space, lifetime=Lifetime.PROCESS)


def register_writers(registry: Registry) -> EgressWriterRefs:
    """Declare the running export's writers, handing back the ``Ref`` of each the area aliases."""
    registry.factory(export_writer, lifetime=Lifetime.PROCESS)
    return EgressWriterRefs(
        configured=registry.factory(configured_writer, lifetime=Lifetime.PROCESS),
        backfill=registry.factory(backfill_writers, lifetime=Lifetime.PROCESS),
    )


def register_writers_absent(registry: Registry) -> None:
    """Declare the writers absent, for a graph whose export is off."""
    for missing in _WRITER_KEYS:
        registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=EXPORT_OFF)


def register_absent(registry: Registry) -> None:
    """Declare the probe and the writers absent, for the store-free graph."""
    for missing in (DirectoryFreeSpace, *_WRITER_KEYS):
        registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
