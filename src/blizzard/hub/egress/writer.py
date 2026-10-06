"""The egress writer seam: one dataset's rows for one date partition become immutable files.

The directory layout, staging, placement, manifests and schemas follow
``blizzard-product:/plans/fact-egress/steps/spec/export.md`` §The directory and §Formats; value encodings follow
``rows.md`` §Shared conventions. This module holds the seam, its value types, and the row validation every binding
shares (``bzh:pluggable-seams``); it names no filesystem and no format library."""

from __future__ import annotations

import secrets
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Context, Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.roles import domain_model

__all__ = [
    "ColumnSpec",
    "ColumnType",
    "DatasetSchema",
    "EgressBatch",
    "EgressFailure",
    "EgressFailureCause",
    "EgressPass",
    "EgressValues",
    "EgressWriterSettings",
    "FilesWritten",
    "IEgressWriter",
    "ManifestCommitted",
    "PlacedFile",
    "mint_process_token",
    "rfc3339_utc",
    "validate_batch",
]

MONEY_PRECISION = 18
MONEY_SCALE = 9
_MONEY_CONTEXT = Context(prec=40, traps=[InvalidOperation])
_MONEY_QUANTUM = Decimal(1).scaleb(-MONEY_SCALE)
_MONEY_BOUND = Decimal(10) ** (MONEY_PRECISION - MONEY_SCALE)
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_TOKEN_ALPHABET = string.ascii_lowercase + string.digits
_TOKEN_LENGTH = 6


class ColumnType(StrEnum):
    STRING = "string"
    INT64 = "int64"
    BOOL = "bool"
    TIMESTAMP = "timestamp"
    MONEY = "money"
    STRING_LIST = "list<string>"


@domain_model
@dataclass(frozen=True)
class ColumnSpec:
    name: str
    type: ColumnType
    nullable: bool
    meaning: str


@domain_model
@dataclass(frozen=True)
class DatasetSchema:
    """A dataset's name, contract major version, and ordered columns."""

    name: str
    major_version: int
    columns: tuple[ColumnSpec, ...]


@domain_model
@dataclass(frozen=True)
class EgressPass:
    """One export pass's identity: the caller's start instant (UTC), whether it is a backfill, and the hub's extractor
    version when the pass writes ``events``."""

    started_at: datetime
    backfill: bool = False
    extractor_version: str | None = None


@domain_model
@dataclass(frozen=True)
class EgressValues:
    """One row's values by column name, with the opaque cursor position it was read at."""

    position: str
    values: Mapping[str, object]


@domain_model
@dataclass(frozen=True)
class EgressBatch:
    """The rows of one dataset that fall in one date partition."""

    schema: DatasetSchema
    partition: date
    egress_pass: EgressPass
    rows: Sequence[EgressValues]


@domain_model
@dataclass(frozen=True)
class EgressWriterSettings:
    max_rows_per_file: int
    min_free_bytes: int = 1024**3


@domain_model
@dataclass(frozen=True)
class PlacedFile:
    """A data file in its final place; ``path`` is relative to the export directory."""

    path: str
    dataset: str
    version: int
    partition: date
    rows: int
    first_position: str
    last_position: str
    sha256: str


class EgressFailureCause(StrEnum):
    LOW_DISK = "low-disk"
    NAME_EXISTS = "name-exists"
    SCHEMA_CONFLICT = "schema-conflict"
    INVALID_ROW = "invalid-row"
    IO_ERROR = "io-error"
    HARD_LINKS_UNSUPPORTED = "hard-links-unsupported"


@domain_model
@dataclass(frozen=True)
class EgressFailure:
    """A write that placed nothing further; ``free_bytes``/``required_bytes`` are set for ``LOW_DISK``."""

    cause: EgressFailureCause
    message: str
    free_bytes: int | None = None
    required_bytes: int | None = None


@domain_model
@dataclass(frozen=True)
class FilesWritten:
    files: tuple[PlacedFile, ...]


@domain_model
@dataclass(frozen=True)
class ManifestCommitted:
    path: str


class IEgressWriter(Protocol):
    """Places a pass's files, then its manifest. Single-caller by contract."""

    def write(self, batch: EgressBatch) -> FilesWritten | EgressFailure:
        """Place ``batch``'s rows as one file per ``max_rows_per_file`` rows. Never replaces an existing name."""
        ...

    def commit_pass(self, egress_pass: EgressPass, placed: Sequence[PlacedFile]) -> ManifestCommitted | EgressFailure:
        """Write the pass's manifest, listing exactly ``placed``. Called after every file is in place."""
        ...


def mint_process_token() -> str:
    """A short lowercase-alphanumeric token, random so two processes started in one second still differ."""
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(_TOKEN_LENGTH))


def validate_batch(batch: EgressBatch) -> EgressFailure | None:
    """The first row value a binding could not hold exactly, or ``None`` when every row conforms."""
    names = [column.name for column in batch.schema.columns]
    for index, row in enumerate(batch.rows):
        if set(row.values) != set(names):
            return _invalid(index, "<row>", f"columns {sorted(row.values)} differ from the schema's {sorted(names)}")
        for column in batch.schema.columns:
            problem = _value_problem(column, row.values[column.name])
            if problem is not None:
                return _invalid(index, column.name, problem)
    return None


def _invalid(index: int, column: str, problem: str) -> EgressFailure:
    return EgressFailure(EgressFailureCause.INVALID_ROW, f"row {index}, column {column}: {problem}")


def _value_problem(column: ColumnSpec, value: object) -> str | None:
    if value is None:
        return None if column.nullable else "null in a non-nullable column"
    match column.type:
        case ColumnType.STRING:
            if not isinstance(value, str):
                return "not a str"
            return _encoding_problem(value)
        case ColumnType.INT64:
            if isinstance(value, bool) or not isinstance(value, int):
                return "not an int"
            return None if _INT64_MIN <= value <= _INT64_MAX else "outside int64"
        case ColumnType.BOOL:
            return None if isinstance(value, bool) else "not a bool"
        case ColumnType.TIMESTAMP:
            if not isinstance(value, datetime):
                return "not a datetime"
            if value.utcoffset() != timedelta(0):
                return "not timezone-aware UTC"
            return None
        case ColumnType.MONEY:
            return _money_problem(value)
        case ColumnType.STRING_LIST:
            if isinstance(value, str) or not isinstance(value, list | tuple):
                return "not a sequence of str"
            if not all(isinstance(item, str) for item in value):
                return "an element is not a str"
            return next((problem for item in value if (problem := _encoding_problem(item))), None)


def _encoding_problem(value: str) -> str | None:
    """A lone surrogate has no UTF-8 form, so Parquet cannot store it and NDJSON would only escape it."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return "not encodable as UTF-8"
    return None


def _money_problem(value: object) -> str | None:
    if not isinstance(value, Decimal):
        return "not a Decimal"
    if not value.is_finite():
        return "not finite"
    try:
        quantized = value.quantize(_MONEY_QUANTUM, context=_MONEY_CONTEXT)
    except InvalidOperation:
        return "does not fit decimal(18, 9)"
    if quantized != value or abs(quantized) >= _MONEY_BOUND:
        return "does not fit decimal(18, 9) exactly"
    return None


def utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def rfc3339_utc(value: datetime) -> str:
    """RFC 3339 in UTC, microsecond precision, ``Z`` suffix."""
    return utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
