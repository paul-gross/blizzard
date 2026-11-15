"""Builds the configured egress writer; the only place that loads the Parquet binding."""

from __future__ import annotations

# Protects the process-local writer cache, not a store decision.
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.domain.observability.egress.ports import IEgressWriter
from blizzard.hub.egress.writer import EgressWriterSettings

__all__ = ["ConfiguredWriter", "EgressUnavailable", "build_egress_writer"]


@domain_model
@dataclass(frozen=True)
class EgressUnavailable:
    reason: str


def build_egress_writer(
    format: Literal["ndjson", "parquet"], directory: Path, settings: EgressWriterSettings, token: str
) -> IEgressWriter | EgressUnavailable:
    """The writer for ``format`` over ``directory``, or *unavailable* when Parquet is asked for without pyarrow."""
    if format == "ndjson":
        from blizzard.hub.egress.internal.ndjson import NdjsonEgressWriter

        return NdjsonEgressWriter(directory, settings, token)
    try:
        from blizzard.hub.egress.internal.parquet import ParquetEgressWriter
    except ImportError:
        return EgressUnavailable("parquet needs pyarrow; install the blizzard[egress] extra")
    return ParquetEgressWriter(directory, settings, token)


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
