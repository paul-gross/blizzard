"""The NDJSON and Parquet bindings on a real directory."""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from blizzard.hub.egress.factory import EgressUnavailable, build_egress_writer
from blizzard.hub.egress.internal.files import DirectoryEgressWriter, schema_document
from blizzard.hub.egress.writer import (
    ColumnSpec,
    ColumnType,
    DatasetSchema,
    EgressBatch,
    EgressFailure,
    EgressFailureCause,
    EgressPass,
    EgressValues,
    EgressWriterSettings,
    FilesWritten,
    IEgressWriter,
    ManifestCommitted,
    PlacedFile,
)
from tests.egress_recipes import LOAD_NDJSON, LOAD_PARQUET, load
from tests.test_egress_writer import PASS, SCHEMA, batch, row

pytestmark = pytest.mark.component

SETTINGS = EgressWriterSettings(max_rows_per_file=2, min_free_bytes=0)
FORMATS = ["ndjson", "parquet"]
EXTENSIONS = {"ndjson": "ndjson.gz", "parquet": "parquet"}


def make(fmt: str, directory: Path, token: str = "k7q2", settings: EgressWriterSettings = SETTINGS) -> IEgressWriter:
    writer = build_egress_writer(fmt, directory, settings, token)  # type: ignore[arg-type]
    assert not isinstance(writer, EgressUnavailable)
    return writer


def files_outside_staging(directory: Path) -> set[str]:
    return {
        p.relative_to(directory).as_posix()
        for p in directory.rglob("*")
        if p.is_file() and ".staging" not in p.relative_to(directory).parts
    }


def read_back(fmt: str, path: Path) -> list[dict[str, object]]:
    if fmt == "ndjson":
        with gzip.open(path, "rt") as handle:
            return [json.loads(line) for line in handle]
    return pq.read_table(path).to_pylist()


def written(result: object) -> tuple[PlacedFile, ...]:
    assert isinstance(result, FilesWritten), result
    return result.files


@pytest.mark.parametrize("fmt", FORMATS)
def test_rows_round_trip_with_nulls_money_lists_and_times(fmt: str, tmp_path: Path) -> None:
    when = datetime(2026, 10, 1, 1, 2, 3, 456789, tzinfo=UTC)
    rows = (
        row("p1", at=when, cost=Decimal("12.5"), tags=["a", "b"]),
        row("p2", n=None, ok=None, at=None, cost=None, tags=None),
    )
    [placed] = written(make(fmt, tmp_path).write(batch(*rows)))
    got = read_back(fmt, tmp_path / placed.path)
    if fmt == "ndjson":
        assert got[0] == {
            "id": "a",
            "n": 1,
            "ok": True,
            "at": "2026-10-01T01:02:03.456789Z",
            "cost": "12.500000000",
            "tags": ["a", "b"],
        }
        assert list(got[0]) == [c.name for c in SCHEMA.columns]
    else:
        assert got[0]["at"] == when
        assert got[0]["cost"] == Decimal("12.5")
        assert got[0]["tags"] == ["a", "b"]
    assert got[1]["n"] is None and got[1]["cost"] is None and got[1]["tags"] is None
    assert placed.path == f"things/v1/date=2026-10-01/things-20261001T061500Z-k7q2-000001.{EXTENSIONS[fmt]}"
    assert placed.sha256 == hashlib.sha256((tmp_path / placed.path).read_bytes()).hexdigest()


def test_parquet_carries_the_declared_arrow_types(tmp_path: Path) -> None:
    [placed] = written(make("parquet", tmp_path).write(batch(row())))
    schema = pq.read_schema(tmp_path / placed.path)
    assert schema.field("at").type == pa.timestamp("us", tz="UTC")
    assert schema.field("cost").type == pa.decimal128(18, 9)
    assert schema.field("tags").type == pa.list_(pa.string())
    assert not schema.field("id").nullable and schema.field("n").nullable
    assert pq.ParquetFile(tmp_path / placed.path).metadata.row_group(0).column(0).compression == "ZSTD"


def test_ndjson_negative_zero_money_and_identical_rows_give_identical_bytes(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    rows = (row(cost=Decimal("-0")),)
    [a] = written(make("ndjson", first).write(batch(*rows)))
    [b] = written(make("ndjson", second).write(batch(*rows)))
    assert (first / a.path).read_bytes() == (second / b.path).read_bytes()
    assert read_back("ndjson", first / a.path)[0]["cost"] == "0.000000000"


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_partition_splits_across_files_with_distinct_names_and_backfill_names_sort_by_pass(
    fmt: str, tmp_path: Path
) -> None:
    writer = make(fmt, tmp_path)
    rows = tuple(row(f"p{i}") for i in range(5))
    files = written(writer.write(batch(*rows)))
    assert [f.rows for f in files] == [2, 2, 1]
    assert [(f.first_position, f.last_position) for f in files] == [("p0", "p1"), ("p2", "p3"), ("p4", "p4")]
    backfill = written(
        writer.write(EgressBatch(SCHEMA, date(2026, 10, 1), EgressPass(PASS.started_at, True), rows[:1]))
    )
    names = [Path(f.path).name for f in (*files, *backfill)]
    assert len(set(names)) == 4
    assert names[-1].endswith(f"-backfill.{EXTENSIONS[fmt]}")
    assert sum(len(read_back(fmt, tmp_path / f.path)) for f in files) == 5


@pytest.mark.parametrize("fmt", FORMATS)
def test_two_writers_with_different_tokens_never_collide(fmt: str, tmp_path: Path) -> None:
    one, two = make(fmt, tmp_path, "aaaa"), make(fmt, tmp_path, "bbbb")
    paths = {f.path for w in (one, two) for f in written(w.write(batch(*(row(f"p{i}") for i in range(3)))))}
    assert len(paths) == 4


def test_a_pre_existing_final_name_fails_the_write_and_keeps_its_bytes(tmp_path: Path) -> None:
    [first] = written(make("ndjson", tmp_path).write(batch(row())))
    original = (tmp_path / first.path).read_bytes()
    result = make("ndjson", tmp_path, "k7q2").write(batch(row("other", id="z")))
    assert isinstance(result, EgressFailure) and result.cause is EgressFailureCause.NAME_EXISTS
    assert (tmp_path / first.path).read_bytes() == original
    assert not list((tmp_path / ".staging").iterdir())


def test_placement_never_replaces_through_rename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("placement used rename/replace")

    monkeypatch.setattr(os, "rename", forbidden)
    monkeypatch.setattr(os, "replace", forbidden)
    assert written(make("ndjson", tmp_path).write(batch(row())))


def test_a_filesystem_without_hard_links_fails_with_a_named_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno
    import os

    def no_link(*args: object, **kwargs: object) -> None:
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", no_link)
    result = make("ndjson", tmp_path).write(batch(row()))
    assert isinstance(result, EgressFailure) and result.cause is EgressFailureCause.HARD_LINKS_UNSUPPORTED
    assert files_outside_staging(tmp_path) == set()


class _Boom:
    extension = "ndjson.gz"

    def __init__(self, error: Exception) -> None:
        self._error = error

    def encode(self, schema: DatasetSchema, rows: object, out: BinaryIO) -> None:
        out.write(b"partial bytes")
        out.flush()
        raise self._error


def test_a_crash_mid_write_leaves_nothing_visible_and_spares_a_foreign_staged_file(tmp_path: Path) -> None:
    (tmp_path / ".staging").mkdir()
    foreign = tmp_path / ".staging" / "zzzz-000001-foreign.ndjson.gz"
    foreign.write_bytes(b"another process")
    crashing = DirectoryEgressWriter(tmp_path, SETTINGS, "k7q2", _Boom(RuntimeError("killed")))
    with pytest.raises(RuntimeError):
        crashing.write(batch(row()))
    assert files_outside_staging(tmp_path) == {"_schema/things.v1.json"}
    assert [p.name for p in (tmp_path / ".staging").iterdir()] == [foreign.name]
    assert not (tmp_path / "_manifests").exists()


def test_an_io_error_mid_write_is_a_failure_that_removes_the_staged_file(tmp_path: Path) -> None:
    failing = DirectoryEgressWriter(tmp_path, SETTINGS, "k7q2", _Boom(OSError(28, "No space left")))
    result = failing.write(batch(row()))
    assert isinstance(result, EgressFailure) and result.cause is EgressFailureCause.IO_ERROR
    assert not list((tmp_path / ".staging").iterdir())
    assert not any(p.name.startswith("things-") for p in tmp_path.rglob("*"))


def test_a_missing_directory_fails_the_write_without_creating_it(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    result = make("ndjson", missing).write(batch(row()))
    assert isinstance(result, EgressFailure) and result.cause is EgressFailureCause.IO_ERROR
    assert not missing.exists()


@pytest.mark.parametrize("fmt", FORMATS)
def test_commit_pass_writes_the_manifest_last_listing_exactly_the_files_passed(fmt: str, tmp_path: Path) -> None:
    writer = make(fmt, tmp_path)
    files = written(writer.write(batch(*(row(f"p{i}") for i in range(3)))))
    committed = writer.commit_pass(PASS, files[:2])
    assert isinstance(committed, ManifestCommitted)
    assert committed.path == f"_manifests/20261001T061500Z-k7q2-{3:06d}.json"
    manifest = json.loads((tmp_path / committed.path).read_text())
    assert [f["path"] for f in manifest["files"]] == [f.path for f in files[:2]]
    for entry in manifest["files"]:
        assert entry["sha256"] == hashlib.sha256((tmp_path / entry["path"]).read_bytes()).hexdigest()
        assert {"dataset", "version", "rows", "first_position", "last_position", "partition"} <= entry.keys()
    assert manifest["backfill"] is False


def test_the_schema_is_placed_once_and_an_identical_existing_one_is_success(tmp_path: Path) -> None:
    make("ndjson", tmp_path, "aaaa").write(batch(row()))
    schema_path = tmp_path / "_schema" / "things.v1.json"
    document = json.loads(schema_path.read_text())
    assert document["dataset"] == "things" and document["major_version"] == 1
    assert document["columns"][4] == {"name": "cost", "type": "money", "nullable": True, "meaning": "money"}
    assert isinstance(make("parquet", tmp_path, "bbbb").write(batch(row())), FilesWritten)


def test_a_different_existing_schema_fails_with_schema_conflict_and_is_untouched(tmp_path: Path) -> None:
    (tmp_path / "_schema").mkdir()
    (tmp_path / "_schema" / "things.v1.json").write_text("{}")
    result = make("ndjson", tmp_path).write(batch(row()))
    assert isinstance(result, EgressFailure) and result.cause is EgressFailureCause.SCHEMA_CONFLICT
    assert (tmp_path / "_schema" / "things.v1.json").read_text() == "{}"
    assert files_outside_staging(tmp_path) == {"_schema/things.v1.json"}


_ID, _N, _OK, _AT, _COST, _TAGS = SCHEMA.columns


def _with_columns(*columns: ColumnSpec) -> DatasetSchema:
    return replace(SCHEMA, columns=columns)


def _plant_schema(directory: Path, schema: DatasetSchema) -> bytes:
    """``schema``'s document where an earlier writer of ``things`` v1 left one."""
    document = schema_document(schema)
    (directory / "_schema").mkdir()
    (directory / "_schema" / "things.v1.json").write_bytes(document)
    return document


@pytest.mark.parametrize("fmt", FORMATS)
def test_an_existing_schema_the_current_one_only_adds_nullable_columns_to_is_replaced(fmt: str, tmp_path: Path) -> None:
    _plant_schema(tmp_path, _with_columns(_ID, _OK, _AT, _COST))
    written = make(fmt, tmp_path).write(batch(row()))
    assert isinstance(written, FilesWritten) and len(written.files) == 1
    assert (tmp_path / "_schema" / "things.v1.json").read_bytes() == schema_document(SCHEMA)
    assert files_outside_staging(tmp_path) == {"_schema/things.v1.json", written.files[0].path}
    assert list((tmp_path / ".staging").iterdir()) == []


_MORE_THAN_NULLABLE_COLUMNS_ADDED = {
    "a non-nullable column added": _with_columns(_N, _OK, _AT, _COST, _TAGS),
    "a column removed": _with_columns(*SCHEMA.columns, ColumnSpec("gone", ColumnType.STRING, True, "removed")),
    "a column retyped": _with_columns(_ID, replace(_N, type=ColumnType.STRING), _OK, _AT, _COST, _TAGS),
    "a meaning changed": _with_columns(_ID, replace(_N, meaning="another count"), _OK, _AT, _COST, _TAGS),
    "a column made nullable": _with_columns(_ID, replace(_N, nullable=False), _OK, _AT, _COST, _TAGS),
    "columns reordered": _with_columns(_ID, _OK, _N, _AT, _COST, _TAGS),
    "another major version": replace(SCHEMA, major_version=0),
}


@pytest.mark.parametrize("older", _MORE_THAN_NULLABLE_COLUMNS_ADDED.values(), ids=_MORE_THAN_NULLABLE_COLUMNS_ADDED)
def test_an_existing_schema_differing_by_more_than_added_nullable_columns_is_a_conflict(
    older: DatasetSchema, tmp_path: Path
) -> None:
    planted = _plant_schema(tmp_path, older)
    result = make("ndjson", tmp_path).write(batch(row()))
    assert isinstance(result, EgressFailure) and result.cause is EgressFailureCause.SCHEMA_CONFLICT
    assert (tmp_path / "_schema" / "things.v1.json").read_bytes() == planted
    assert files_outside_staging(tmp_path) == {"_schema/things.v1.json"}


@pytest.mark.parametrize("fmt", FORMATS)
def test_below_min_free_bytes_nothing_is_written(fmt: str, tmp_path: Path) -> None:
    settings = EgressWriterSettings(max_rows_per_file=2, min_free_bytes=2**62)
    writer = make(fmt, tmp_path, settings=settings)
    for result in (writer.write(batch(row())), writer.commit_pass(PASS, ())):
        assert isinstance(result, EgressFailure) and result.cause is EgressFailureCause.LOW_DISK
        assert result.required_bytes == 2**62 and result.free_bytes is not None and result.free_bytes < 2**62
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("fmt", FORMATS)
def test_an_invalid_row_writes_nothing(fmt: str, tmp_path: Path) -> None:
    result = make(fmt, tmp_path).write(batch(row("p1"), row("p2", cost=1.5)))
    assert isinstance(result, EgressFailure) and result.cause is EgressFailureCause.INVALID_ROW
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("poison", ["a\ud800", ["ok", "b\udfff"]])
def test_a_lone_surrogate_is_an_invalid_row_in_both_formats(fmt: str, poison: object, tmp_path: Path) -> None:
    column = "tags" if isinstance(poison, list) else "id"
    result = make(fmt, tmp_path).write(batch(row("p1", **{column: poison})))
    assert isinstance(result, EgressFailure) and result.cause is EgressFailureCause.INVALID_ROW
    assert result.message == f"row 0, column {column}: not encodable as UTF-8"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("fmt", FORMATS)
def test_the_docs_load_recipe_reads_only_the_files_a_manifest_names(fmt: str, tmp_path: Path) -> None:
    writer = make(fmt, tmp_path)
    written = writer.write(batch(row("p1", id="named-1"), row("p2", id="named-2")))
    assert isinstance(written, FilesWritten)
    assert isinstance(writer.commit_pass(PASS, written.files), ManifestCommitted)
    # A sibling in the same partition, copied from a placed file under another name, that no manifest lists.
    placed = tmp_path / written.files[0].path
    stray = placed.with_name(f"stray-{placed.name}")
    stray.write_bytes(placed.read_bytes())
    stray_rows = read_back(fmt, stray)
    assert stray_rows == read_back(fmt, placed)

    connection = duckdb.connect()
    load(connection, LOAD_NDJSON if fmt == "ndjson" else LOAD_PARQUET, tmp_path, SCHEMA.name)

    assert connection.execute(f"SELECT id FROM {SCHEMA.name} ORDER BY id").fetchall() == [("named-1",), ("named-2",)]


def test_parquet_is_unavailable_without_pyarrow_and_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    monkeypatch.delitem(sys.modules, "blizzard.hub.egress.internal.parquet", raising=False)
    result = build_egress_writer("parquet", tmp_path, SETTINGS, "k7q2")
    assert isinstance(result, EgressUnavailable)
    assert "blizzard[egress]" in result.reason
    assert not isinstance(build_egress_writer("ndjson", tmp_path, SETTINGS, "k7q2"), EgressUnavailable)


@pytest.mark.parametrize("fmt", FORMATS)
def test_the_docs_load_recipe_reads_files_from_before_a_nullable_column_was_added_as_null_in_it(
    fmt: str, tmp_path: Path
) -> None:
    older = _with_columns(_ID, _N, _OK, _AT, _COST)
    for token, schema, minute, id_ in (("aaaa", older, 15, "before"), ("bbbb", SCHEMA, 16, "after")):
        values = {column.name: row().values[column.name] for column in schema.columns} | {"id": id_}
        egress_pass = EgressPass(datetime(2026, 10, 1, 6, minute, tzinfo=UTC))
        writer = make(fmt, tmp_path, token)
        written = writer.write(EgressBatch(schema, date(2026, 10, 1), egress_pass, (EgressValues(id_, values),)))
        assert isinstance(written, FilesWritten)
        assert isinstance(writer.commit_pass(egress_pass, written.files), ManifestCommitted)

    connection = duckdb.connect()
    load(connection, LOAD_NDJSON if fmt == "ndjson" else LOAD_PARQUET, tmp_path, SCHEMA.name)

    rows = connection.execute(f"SELECT id, tags FROM {SCHEMA.name} ORDER BY id").fetchall()
    assert rows == [("after", ["x"]), ("before", None)]
