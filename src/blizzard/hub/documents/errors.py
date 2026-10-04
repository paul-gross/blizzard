"""The one error every config codec binding raises on a document it cannot decode."""

from __future__ import annotations


class ConfigDecodeError(ValueError):
    """A document that does not decode to a mapping — malformed syntax, a duplicate key, or a root that
    is not a mapping. ``line`` and ``column`` are 1-based and ``None`` when the parser gave no position."""

    def __init__(self, problem: str, *, line: int | None = None, column: int | None = None) -> None:
        where = f" (line {line}, column {column})" if line is not None and column is not None else ""
        super().__init__(f"{problem}{where}")
        self.problem = problem
        self.line = line
        self.column = column
