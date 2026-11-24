"""Builds the configured egress writer; the only place that loads the Parquet binding."""

from __future__ import annotations

# Protects the process-local writer cache, not a store decision.
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Literal

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.observability.egress.boot import EgressBoot, EgressPathKey
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.domain.observability.egress.ports import IEgressWriter
from blizzard.hub.egress.writer import EgressWriterSettings, mint_process_token

__all__ = [
    "BackfillWriters",
    "ConfiguredWriter",
    "EgressUnavailable",
    "build_egress_writer",
    "egress_boot",
    "running_writer",
    "writer_settings",
    "writer_unavailable",
]


@domain_model
@dataclass(frozen=True)
class EgressUnavailable:
    reason: str


def writer_unavailable(format: Literal["ndjson", "parquet"]) -> EgressUnavailable | None:
    """Why ``format``'s writer cannot be built — Parquet without pyarrow — or ``None`` when it can."""
    if format == "ndjson":
        return None
    try:
        import_module("blizzard.hub.egress.internal.parquet")
    except ImportError:
        return EgressUnavailable("parquet needs pyarrow; install the blizzard[egress] extra")
    return None


def egress_boot(config: EgressConfig, key: EgressPathKey | None) -> EgressBoot:
    """The export as the hub boots it over ``config`` and the path hash ``key``, none when unset: off with no
    directory, rejected when its format's writer is unavailable, else on."""
    key = key if key is not None else EgressPathKey()
    if config.directory is None:
        return EgressBoot(config, key)
    unavailable = writer_unavailable(config.format)
    return EgressBoot(config, key, rejected=unavailable.reason if unavailable is not None else None)


def writer_settings(config: EgressConfig) -> EgressWriterSettings:
    """The file-size and free-space settings ``config`` writes under."""
    return EgressWriterSettings(config.max_rows_per_file, config.min_free_bytes)


def build_egress_writer(
    format: Literal["ndjson", "parquet"], directory: Path, settings: EgressWriterSettings, token: str
) -> IEgressWriter | EgressUnavailable:
    """The writer for ``format`` over ``directory``, or *unavailable* when Parquet is asked for without pyarrow."""
    unavailable = writer_unavailable(format)
    if unavailable is not None:
        return unavailable
    if format == "ndjson":
        from blizzard.hub.egress.internal.ndjson import NdjsonEgressWriter

        return NdjsonEgressWriter(directory, settings, token)
    from blizzard.hub.egress.internal.parquet import ParquetEgressWriter

    return ParquetEgressWriter(directory, settings, token)


def running_writer(config: EgressConfig, settings: EgressWriterSettings) -> IEgressWriter:
    """A fresh writer over ``config``'s format and directory with a newly minted process token. Built only once the
    export is on, so its directory is set and its format's writer is available."""
    directory = config.directory
    assert directory is not None
    built = build_egress_writer(config.format, directory, settings, mint_process_token())
    assert not isinstance(built, EgressUnavailable)
    return built


class BackfillWriters:
    """A fresh writer per backfill, over the config the backfill reads."""

    def __call__(self, config: EgressConfig) -> IEgressWriter:
        return running_writer(config, writer_settings(config))


class ConfiguredWriter:
    """Keep a writer until its file-size or free-space settings change."""

    def __init__(
        self,
        writer: IEgressWriter,
        settings: EgressWriterSettings,
        build: Callable[[EgressWriterSettings], IEgressWriter],
    ) -> None:
        self._writer = writer
        self._settings = settings
        self._build = build
        self._lock = threading.Lock()

    def for_config(self, config: EgressConfig) -> IEgressWriter:
        settings = EgressWriterSettings(config.max_rows_per_file, config.min_free_bytes)
        with self._lock:
            if settings != self._settings:
                self._writer = self._build(settings)
                self._settings = settings
            return self._writer
