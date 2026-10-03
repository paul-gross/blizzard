"""Builds the configured egress writer; the only place that loads the Parquet binding."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from blizzard.hub.egress.writer import EgressWriterSettings, IEgressWriter

__all__ = ["EgressUnavailable", "build_egress_writer"]


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
