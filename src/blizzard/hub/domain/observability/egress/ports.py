"""The egress writer port and the vocabulary its signature names, owned by the observability domain that drives it.

The directory layout, staging, placement, manifests and schemas follow
``blizzard-product:/delivered/fact-egress/steps/spec/export.md`` §The directory and §Formats; value encodings follow
``blizzard-product:/delivered/fact-egress/steps/spec/rows.md`` §Shared conventions. The egress package implements
:class:`IEgressWriter` (``bzh:dependency-inversion``); this module names no filesystem or format library."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
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
    "FilesWritten",
    "IEgressWriter",
    "ManifestCommitted",
    "PlacedFile",
    "rfc3339_utc",
    "utc",
]


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

    def widens(self, older: DatasetSchema) -> bool:
        """Whether this schema is ``older`` with nullable columns added and nothing else changed — the one schema
        change that keeps a major version, so the export carries on under ``older``'s files."""
        if (self.name, self.major_version) != (older.name, older.major_version):
            return False
        kept = {c.name for c in older.columns}
        added = [c for c in self.columns if c.name not in kept]
        return all(c.nullable for c in added) and [c for c in self.columns if c.name in kept] == list(older.columns)


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
        """Place ``batch``'s rows as one file per ``max_rows_per_file`` rows. Never replaces a data file; swaps
        the dataset's ``_schema/`` document in place when the current schema only adds nullable columns to it."""
        ...

    def commit_pass(self, egress_pass: EgressPass, placed: Sequence[PlacedFile]) -> ManifestCommitted | EgressFailure:
        """Write the pass's manifest, listing exactly ``placed``. Called after every file is in place."""
        ...


def utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def rfc3339_utc(value: datetime) -> str:
    """RFC 3339 in UTC, microsecond precision, ``Z`` suffix."""
    return utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
