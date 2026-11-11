"""The egress writer's settings and the shared row validation every binding applies.

The seam itself — :class:`IEgressWriter` and the value types its signature names — is the observability domain's, in
``blizzard.hub.domain.observability.egress.ports`` (``bzh:dependency-inversion``). Value encodings follow
``blizzard-product:/delivered/fact-egress/steps/spec/rows.md`` §Shared conventions; this module names no filesystem or
format library."""

from __future__ import annotations

import secrets
import string
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Context, Decimal, InvalidOperation
from typing import cast

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.observability.egress.ports import (
    ColumnSpec,
    ColumnType,
    EgressBatch,
    EgressFailure,
    EgressFailureCause,
)

__all__ = [
    "EgressWriterSettings",
    "mint_process_token",
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


@domain_model
@dataclass(frozen=True)
class EgressWriterSettings:
    max_rows_per_file: int
    min_free_bytes: int = 1024**3


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
            items = cast("Sequence[object]", value)
            texts = [item for item in items if isinstance(item, str)]
            if len(texts) != len(items):
                return "an element is not a str"
            return next((problem for item in texts if (problem := _encoding_problem(item))), None)


def _encoding_problem(value: str) -> str | None:
    """A lone surrogate has no UTF-8 form."""
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
