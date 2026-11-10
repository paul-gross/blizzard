"""Classify an ingest source from facts observed by the host edge."""

from __future__ import annotations

from enum import StrEnum

from blizzard.runner.transfer.refusals import SourceUnsupported

SQLITE_HEADER = b"SQLite format 3\x00"


class SourceKind(StrEnum):
    """What an ingest source is."""

    HOST_DIRECTORY = "host directory"
    ARCHIVE = "archive store"
    UNGRADUATED = "runner directory that has not graduated"


class DirectoryState(StrEnum):
    """The host configuration observed in a source directory."""

    GRADUATED = "graduated"
    UNGRADUATED = "ungraduated"
    UNINITIALISED = "uninitialised"


def classify_source(
    spelling: str, *, directory: DirectoryState | None, is_file: bool, header: bytes, host_config_filename: str
) -> SourceKind:
    """Decide the source kind or explain why the observed source cannot be ingested."""
    if directory is not None:
        if directory is DirectoryState.GRADUATED:
            return SourceKind.HOST_DIRECTORY
        if directory is DirectoryState.UNGRADUATED:
            return SourceKind.UNGRADUATED
        raise SourceUnsupported(f"{spelling} holds no {host_config_filename}, so it is not a host directory")
    if is_file:
        if header == SQLITE_HEADER:
            return SourceKind.ARCHIVE
        raise SourceUnsupported(f"{spelling} is not a SQLite store, so it is not an archive `runner store reset` wrote")
    raise SourceUnsupported(f"{spelling} does not exist")
