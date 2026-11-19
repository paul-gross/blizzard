"""A cursor-paginated list read kept as the hub sent it, for a verb whose ``--json`` prints the rows raw."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, cast

from pydantic import TypeAdapter

from blizzard.hub.cli.context import CliContext


class _CursorPage(Protocol):
    @property
    def next_cursor(self) -> str | None: ...


def read_pages[P: _CursorPage](
    cli: CliContext,
    path: str,
    operation: str,
    *,
    page: TypeAdapter[P],
    key: str,
    params: dict[str, str] | None = None,
) -> tuple[list[Any], list[P]]:
    """Every page of ``path``, as :meth:`CliContext.get_all` reads them, with the raw ``key`` rows of every page
    concatenated beside the decoded pages — ``--json`` prints the raw rows unchanged."""
    page_params = dict(params or {})
    page_params["limit"] = "1000"
    raw_rows: list[Any] = []
    pages: list[P] = []
    cursor: str | None = None
    while True:
        if cursor is not None:
            page_params["cursor"] = cursor
        raw, value = cli.decode(cli.get(path, operation, params=page_params), page, operation)
        raw_rows.extend(cast(Sequence[Any], cast(dict[str, Any], raw).get(key) or []))
        pages.append(value)
        cursor = value.next_cursor
        if cursor is None:
            return raw_rows, pages
