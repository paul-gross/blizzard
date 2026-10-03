"""The Parquet binding: zstd-compressed, the Arrow schema built from the dataset schema, never inferred.

The one module that imports ``pyarrow``; only the factory loads it."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO

import pyarrow as pa
import pyarrow.parquet as pq

from blizzard.hub.egress.internal.files import DirectoryEgressWriter
from blizzard.hub.egress.writer import (
    MONEY_PRECISION,
    MONEY_SCALE,
    ColumnType,
    DatasetSchema,
    EgressRow,
    EgressWriterSettings,
    IEgressWriter,
)

_ARROW_TYPES: dict[ColumnType, pa.DataType] = {
    ColumnType.STRING: pa.string(),
    ColumnType.INT64: pa.int64(),
    ColumnType.BOOL: pa.bool_(),
    ColumnType.TIMESTAMP: pa.timestamp("us", tz="UTC"),
    ColumnType.MONEY: pa.decimal128(MONEY_PRECISION, MONEY_SCALE),
    ColumnType.STRING_LIST: pa.list_(pa.string()),
}


def arrow_schema(schema: DatasetSchema) -> pa.Schema:
    return pa.schema([pa.field(c.name, _ARROW_TYPES[c.type], nullable=c.nullable) for c in schema.columns])


class ParquetEncoder:
    extension = "parquet"

    def encode(self, schema: DatasetSchema, rows: Sequence[EgressRow], out: BinaryIO) -> None:
        arrays = [
            pa.array(
                [list(v) if isinstance(v, tuple) else v for v in (row.values[c.name] for row in rows)],
                type=_ARROW_TYPES[c.type],
            )
            for c in schema.columns
        ]
        table = pa.Table.from_arrays(arrays, schema=arrow_schema(schema))
        pq.write_table(table, out, compression="zstd")


class ParquetEgressWriter(DirectoryEgressWriter):
    def __init__(self, directory: Path, settings: EgressWriterSettings, token: str) -> None:
        super().__init__(directory, settings, token, ParquetEncoder())


def _conforms_parquet(x: ParquetEgressWriter) -> IEgressWriter:
    return x
