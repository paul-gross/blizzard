"""Egress proof for the e2e tier: reads a fact-egress directory in either format back the way a consumer would.

Rows are decoded into one canonical shape — times as RFC 3339 strings, money as nine-place strings — so the two
formats compare equal. The newest copy of an identity is the copy with the latest ``exported_at``. DuckDB runs the
published views and the docs' recipes over the files themselves."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq

from blizzard.hub.egress.writer import rfc3339_utc
from tests.egress_recipes import COST_BY_NODE_BY_DAY, SLOWEST_STATION_OF_THE_WEEK, recipe
from tests.repo_files import repo_root

DATASETS = {"steps": "step_key", "invocations": "usage_id"}
SUFFIX = {"ndjson": ".ndjson.gz", "parquet": ".parquet"}
_CONTRACT_DIR = repo_root() / "contracts" / "egress"

Row = dict[str, Any]
Station = tuple[str, str]


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        return rfc3339_utc(value)
    if isinstance(value, Decimal):
        return f"{value:.9f}"
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return value


def data_files(directory: Path, fmt: str, dataset: str) -> list[Path]:
    """Every data file of ``dataset``, live and backfilled, in name order."""
    return sorted((directory / dataset / "v1").glob(f"*/*{SUFFIX[fmt]}"))


def decode(path: Path) -> list[Row]:
    """The rows of one data file, in the canonical shape."""
    if path.name.endswith(SUFFIX["parquet"]):
        return [{k: _canonical(v) for k, v in row.items()} for row in pq.read_table(path).to_pylist()]
    with gzip.open(path, "rt") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def read_rows(directory: Path, fmt: str, *, backfill: bool | None = None) -> dict[str, list[Row]]:
    """Every row of every dataset. ``backfill`` narrows to the backfilled files, or to the live ones."""
    return {
        dataset: [
            row
            for path in data_files(directory, fmt, dataset)
            if backfill is None or ("backfill" in path.name) == backfill
            for row in decode(path)
        ]
        for dataset in DATASETS
    }


def newest(rows: Sequence[Row], identity: str) -> dict[Any, Row]:
    """The newest copy of each identity."""
    kept: dict[Any, Row] = {}
    for row in rows:
        held = kept.get(row[identity])
        if held is None or row["exported_at"] > held["exported_at"]:
            kept[row[identity]] = row
    return kept


def newest_without_export_time(rows: Mapping[str, Sequence[Row]]) -> dict[str, dict[Any, Row]]:
    """Each dataset's newest copies keyed by identity, with the one column that differs per copy dropped."""
    return {
        dataset: {
            key: {k: v for k, v in row.items() if k != "exported_at"}
            for key, row in newest(rows[dataset], identity).items()
        }
        for dataset, identity in DATASETS.items()
    }


def assert_manifests_name_every_file(directory: Path, fmt: str) -> None:
    """Every data file is named by exactly one manifest, with its row count and SHA-256, and every file a manifest
    names exists."""
    listed: dict[str, Mapping[str, Any]] = {}
    for manifest in sorted((directory / "_manifests").glob("*.json")):
        for entry in json.loads(manifest.read_text())["files"]:
            assert entry["path"] not in listed, f"{entry['path']} is named by two manifests"
            listed[entry["path"]] = entry
    on_disk = {
        path.relative_to(directory).as_posix() for dataset in DATASETS for path in data_files(directory, fmt, dataset)
    }
    assert on_disk, f"no {fmt} data files in {directory}"
    assert set(listed) == on_disk, f"manifests and files disagree: {set(listed) ^ on_disk}"
    for relative, entry in listed.items():
        path = directory / relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"], f"{relative} sha256"
        assert len(decode(path)) == entry["rows"], f"{relative} row count"


def assert_nothing_planted(directory: Path, fmt: str, planted: Mapping[str, Sequence[str]]) -> None:
    """No planted value is in any decoded row, manifest or schema document. A kind with nothing planted fails: that
    scan would pass empty. Compressed bytes are never grepped; they would pass vacuously."""
    texts = [
        json.dumps(row, ensure_ascii=False)
        for dataset in DATASETS
        for path in data_files(directory, fmt, dataset)
        for row in decode(path)
    ]
    texts += [p.read_text() for sub in ("_manifests", "_schema") for p in sorted((directory / sub).glob("*.json"))]
    assert len(texts) > len(DATASETS), "the scan read no rows"
    for kind, values in planted.items():
        assert values and all(values), f"nothing was planted for {kind}: the scan would pass vacuously"
        for value in values:
            assert not any(value in text for text in texts), f"the export carries a planted {kind}: {value!r}"


def warehouse(directory: Path, fmt: str) -> duckdb.DuckDBPyConnection:
    """DuckDB over the directory in place: each dataset bound to its files, and the dictionary's newest-copy view
    over it as ``<dataset>_newest``, which is what the docs' recipes read."""
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    for dataset in DATASETS:
        pattern = f"{directory}/{dataset}/v1/*/*{SUFFIX[fmt]}"
        reader = (
            f"read_json_auto('{pattern}', format = 'newline_delimited')"
            if fmt == "ndjson"
            else f"read_parquet('{pattern}')"
        )
        connection.execute(f"CREATE VIEW {dataset} AS SELECT * FROM {reader}")
        view = (_CONTRACT_DIR / f"{dataset}_newest.sql").read_text().strip()
        connection.execute(f"CREATE VIEW {dataset}_newest AS {view}")
    return connection


def published_newest_identities(directory: Path, fmt: str) -> dict[str, set[Any]]:
    """The identities the published newest-copy views return."""
    connection = warehouse(directory, fmt)
    return {
        dataset: {row[0] for row in connection.execute(f"SELECT {identity} FROM {dataset}_newest").fetchall()}
        for dataset, identity in DATASETS.items()
    }


def cost_by_station(directory: Path, fmt: str) -> dict[Station, tuple[Decimal, Decimal]]:
    """The cost recipe's billed and estimated cost per station, summed over its days."""
    rows = warehouse(directory, fmt).execute(recipe(COST_BY_NODE_BY_DAY)).fetchall()
    totals: dict[Station, tuple[Decimal, Decimal]] = {}
    for graph, node, _day, billed, estimated in rows:
        held = totals.get((graph, node), (Decimal(0), Decimal(0)))
        totals[(graph, node)] = (held[0] + (billed or Decimal(0)), held[1] + (estimated or Decimal(0)))
    return totals


def slowest_station(directory: Path, fmt: str) -> Station:
    """The station the slowest-of-the-week recipe names."""
    ((graph, node, _mean),) = warehouse(directory, fmt).execute(recipe(SLOWEST_STATION_OF_THE_WEEK)).fetchall()
    return graph, node
