"""The format-neutral directory discipline both file bindings share (``bzh:pluggable-seams``).

Stage under ``.staging/``, flush and fsync, place with ``os.link`` (which fails with ``EEXIST`` instead of
replacing), fsync the parent, then drop the staged name. ``rename`` is never used: it overwrites. Only files this
writer staged are ever removed; ``.staging/`` is shared with other processes and is never cleared wholesale."""

from __future__ import annotations

import errno
import hashlib
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Protocol

from blizzard.foundation.roles import dto
from blizzard.hub.egress.space import free_bytes
from blizzard.hub.egress.writer import (
    DatasetSchema,
    EgressBatch,
    EgressFailure,
    EgressFailureCause,
    EgressPass,
    EgressValues,
    EgressWriterSettings,
    FilesWritten,
    ManifestCommitted,
    PlacedFile,
    rfc3339_utc,
    utc,
    validate_batch,
)

_STAGING = ".staging"
_NO_HARD_LINKS = {errno.EPERM, errno.ENOTSUP, errno.EXDEV, errno.ENOSYS}


class RowEncoder(Protocol):
    extension: str

    def encode(self, schema: DatasetSchema, rows: Sequence[EgressValues], out: BinaryIO) -> None: ...


@dto
@dataclass(frozen=True)
class _Placed:
    sha256: str


def _pass_stamp(started_at: datetime) -> str:
    return utc(started_at).strftime("%Y%m%dT%H%M%SZ")


def schema_document(schema: DatasetSchema) -> bytes:
    document = {
        "dataset": schema.name,
        "major_version": schema.major_version,
        "columns": [
            {"name": c.name, "type": str(c.type), "nullable": c.nullable, "meaning": c.meaning} for c in schema.columns
        ],
    }
    return (json.dumps(document, indent=2) + "\n").encode()


class DirectoryEgressWriter:
    """An ``IEgressWriter`` over a directory; the encoder supplies the format."""

    def __init__(self, directory: Path, settings: EgressWriterSettings, token: str, encoder: RowEncoder) -> None:
        self._root = directory
        self._settings = settings
        self._token = token
        self._encoder = encoder
        self._sequence = 0
        self._schemas_placed: set[tuple[str, int]] = set()

    def write(self, batch: EgressBatch) -> FilesWritten | EgressFailure:
        if (invalid := validate_batch(batch)) is not None:
            return invalid
        if (low := self._disk_guard()) is not None:
            return low
        if not batch.rows:
            return FilesWritten(())
        if (conflict := self._place_schema(batch.schema)) is not None:
            return conflict
        schema = batch.schema
        size = self._settings.max_rows_per_file
        placed: list[PlacedFile] = []
        for start in range(0, len(batch.rows), size):
            rows = batch.rows[start : start + size]
            relative = Path(schema.name) / f"v{schema.major_version}" / f"date={batch.partition!s}"
            name = self._name(schema.name, batch.egress_pass, self._encoder.extension)
            path = relative / name

            def body(out: BinaryIO, rows: Sequence[EgressValues] = rows) -> None:
                self._encoder.encode(schema, rows, out)

            result = self._place(path, body)
            if isinstance(result, EgressFailure):
                return result
            placed.append(
                PlacedFile(
                    path=path.as_posix(),
                    dataset=schema.name,
                    version=schema.major_version,
                    partition=batch.partition,
                    rows=len(rows),
                    first_position=rows[0].position,
                    last_position=rows[-1].position,
                    sha256=result.sha256,
                )
            )
        return FilesWritten(tuple(placed))

    def commit_pass(self, egress_pass: EgressPass, placed: Sequence[PlacedFile]) -> ManifestCommitted | EgressFailure:
        if (low := self._disk_guard()) is not None:
            return low
        document: dict[str, object] = {
            "pass_started_at": rfc3339_utc(egress_pass.started_at),
            "backfill": egress_pass.backfill,
        }
        if egress_pass.extractor_version is not None:
            document["extractor_version"] = egress_pass.extractor_version
        document["files"] = [
            {
                "path": f.path,
                "dataset": f.dataset,
                "version": f.version,
                "partition": str(f.partition),
                "rows": f.rows,
                "first_position": f.first_position,
                "last_position": f.last_position,
                "sha256": f.sha256,
            }
            for f in placed
        ]
        encoded = (json.dumps(document, indent=2) + "\n").encode()
        path = Path("_manifests") / self._name(None, egress_pass, "json")
        result = self._place(path, lambda out: _write_all(out, encoded))
        if isinstance(result, EgressFailure):
            return result
        return ManifestCommitted(path.as_posix())

    def _name(self, dataset: str | None, egress_pass: EgressPass, extension: str) -> str:
        self._sequence += 1
        stem = f"{_pass_stamp(egress_pass.started_at)}-{self._token}-{self._sequence:06d}"
        if dataset is not None:
            stem = f"{dataset}-{stem}"
        return f"{stem}{'-backfill' if egress_pass.backfill else ''}.{extension}"

    def _disk_guard(self) -> EgressFailure | None:
        free = free_bytes(self._root)
        if free is None:
            return EgressFailure(EgressFailureCause.IO_ERROR, f"cannot read free space of {self._root}")
        required = self._settings.min_free_bytes
        if free < required:
            return EgressFailure(
                EgressFailureCause.LOW_DISK,
                f"{free} bytes free on {self._root}, {required} required",
                free_bytes=free,
                required_bytes=required,
            )
        return None

    def _place_schema(self, schema: DatasetSchema) -> EgressFailure | None:
        key = (schema.name, schema.major_version)
        if key in self._schemas_placed:
            return None
        document = schema_document(schema)
        path = Path("_schema") / f"{schema.name}.v{schema.major_version}.json"
        result = self._place(path, lambda out: _write_all(out, document))
        if isinstance(result, EgressFailure):
            if result.cause is not EgressFailureCause.NAME_EXISTS:
                return result
            if (self._root / path).read_bytes() != document:
                return EgressFailure(EgressFailureCause.SCHEMA_CONFLICT, f"{path} exists with different content")
        self._schemas_placed.add(key)
        return None

    def _place(self, relative: Path, body: Callable[[BinaryIO], None]) -> _Placed | EgressFailure:
        final = self._root / relative
        staging = self._root / _STAGING
        staged = staging / f"{self._token}-{self._sequence:06d}-{relative.name}"
        try:
            staging.mkdir(exist_ok=True)
            final.parent.mkdir(parents=True, exist_ok=True)
            with staged.open("xb") as out:
                body(out)
                out.flush()
                os.fsync(out.fileno())
            digest = hashlib.sha256(staged.read_bytes()).hexdigest()
            try:
                os.link(staged, final)
            except FileExistsError:
                return EgressFailure(EgressFailureCause.NAME_EXISTS, f"{relative} already exists")
            except OSError as error:
                if error.errno in _NO_HARD_LINKS:
                    return EgressFailure(
                        EgressFailureCause.HARD_LINKS_UNSUPPORTED,
                        f"{self._root} does not support hard links ({error}); placement never falls back to rename",
                    )
                raise
            _fsync_directory(final.parent)
            return _Placed(digest)
        except OSError as error:
            return EgressFailure(EgressFailureCause.IO_ERROR, f"{relative}: {error}")
        finally:
            staged.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(out: BinaryIO, content: bytes) -> None:
    out.write(content)
