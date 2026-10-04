"""The egress seam's pure rules — row validation, tokens, result values — against the in-memory writer."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from blizzard.hub.egress.writer import (
    ColumnSpec,
    ColumnType,
    DatasetSchema,
    EgressBatch,
    EgressFailure,
    EgressFailureCause,
    EgressPass,
    EgressValues,
    FilesWritten,
    ManifestCommitted,
    mint_process_token,
)
from tests.support import InMemoryEgressWriter

pytestmark = pytest.mark.unit

SCHEMA = DatasetSchema(
    "things",
    1,
    (
        ColumnSpec("id", ColumnType.STRING, False, "the id"),
        ColumnSpec("n", ColumnType.INT64, True, "a count"),
        ColumnSpec("ok", ColumnType.BOOL, True, "a flag"),
        ColumnSpec("at", ColumnType.TIMESTAMP, True, "a time"),
        ColumnSpec("cost", ColumnType.MONEY, True, "money"),
        ColumnSpec("tags", ColumnType.STRING_LIST, True, "labels"),
    ),
)
PASS = EgressPass(datetime(2026, 10, 1, 6, 15, tzinfo=UTC))


def row(position: str = "p1", /, **overrides: object) -> EgressValues:
    values: dict[str, object] = {
        "id": "a",
        "n": 1,
        "ok": True,
        "at": datetime(2026, 10, 1, tzinfo=UTC),
        "cost": Decimal("1.5"),
        "tags": ["x"],
    }
    values.update(overrides)
    return EgressValues(position, values)


def batch(*rows: EgressValues) -> EgressBatch:
    return EgressBatch(SCHEMA, date(2026, 10, 1), PASS, rows)


def test_a_conforming_batch_is_recorded_and_its_manifest_lists_what_was_passed() -> None:
    writer = InMemoryEgressWriter()
    written = writer.write(batch(row("p1"), row("p2", n=None, cost=None, tags=None)))
    assert isinstance(written, FilesWritten)
    assert (written.files[0].first_position, written.files[0].last_position) == ("p1", "p2")
    committed = writer.commit_pass(PASS, written.files)
    assert isinstance(committed, ManifestCommitted)
    assert writer.manifests == [(PASS, written.files)]


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": None},
        {"id": 3},
        {"n": True},
        {"n": 2**63},
        {"ok": 1},
        {"at": datetime(2026, 10, 1)},
        {"at": datetime(2026, 10, 1, tzinfo=timezone(timedelta(hours=2)))},
        {"at": "2026-10-01"},
        {"cost": 1.5},
        {"cost": Decimal("NaN")},
        {"cost": Decimal("0.0000000001")},
        {"cost": Decimal("1000000000")},
        {"tags": "abc"},
        {"tags": ["a", 1]},
    ],
)
def test_a_value_a_format_could_not_hold_exactly_is_an_invalid_row(overrides: dict[str, object]) -> None:
    result = InMemoryEgressWriter().write(batch(row(**overrides)))
    assert isinstance(result, EgressFailure)
    assert result.cause is EgressFailureCause.INVALID_ROW


def test_a_row_with_missing_or_extra_columns_is_invalid() -> None:
    writer = InMemoryEgressWriter()
    assert isinstance(writer.write(batch(EgressValues("p", {"id": "a"}))), EgressFailure)
    assert isinstance(writer.write(batch(row(extra=1))), EgressFailure)
    assert writer.batches == []


def test_money_at_the_decimal_bounds_is_valid() -> None:
    result = InMemoryEgressWriter().write(
        batch(row(cost=Decimal("999999999.999999999")), row(cost=Decimal("-0.000000001")))
    )
    assert isinstance(result, FilesWritten)


def test_process_tokens_are_short_lowercase_alphanumerics_and_differ() -> None:
    tokens = {mint_process_token() for _ in range(50)}
    assert len(tokens) > 1
    assert all(t.isalnum() and t == t.lower() and len(t) == 6 for t in tokens)
