"""The NDJSON binding: gzip-compressed, one JSON object per row, columns in schema order.

Output is deterministic — gzip mtime 0 and a fixed encoding — so identical rows give identical bytes."""

from __future__ import annotations

import gzip
import json
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO

from blizzard.hub.egress.internal.files import DirectoryEgressWriter
from blizzard.hub.egress.writer import (
    MONEY_SCALE,
    DatasetSchema,
    EgressRow,
    EgressWriterSettings,
    IEgressWriter,
    rfc3339_utc,
)


def _encode_value(value: object) -> object:
    if isinstance(value, datetime):
        return rfc3339_utc(value)
    if isinstance(value, Decimal):
        return format(abs(value) if value == 0 else value, f".{MONEY_SCALE}f")
    if isinstance(value, tuple):
        return list(value)
    return value


class NdjsonEncoder:
    extension = "ndjson.gz"

    def encode(self, schema: DatasetSchema, rows: Sequence[EgressRow], out: BinaryIO) -> None:
        with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0) as gz:
            for row in rows:
                line = {c.name: _encode_value(row.values[c.name]) for c in schema.columns}
                gz.write(json.dumps(line, separators=(",", ":")).encode() + b"\n")


class NdjsonEgressWriter(DirectoryEgressWriter):
    def __init__(self, directory: Path, settings: EgressWriterSettings, token: str) -> None:
        super().__init__(directory, settings, token, NdjsonEncoder())


def _conforms_ndjson(x: NdjsonEgressWriter) -> IEgressWriter:
    return x
