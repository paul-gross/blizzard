"""The permit listing a verb reads through: it pages the hub's permits and reports what it read as it closes."""

# exemplar: bzh:dependency-injection, bzh:clients-decode-answers — built from the command's ``CliContext`` alone.

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import click
from pydantic import TypeAdapter

from blizzard.foundation.roles import dto
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.project_option import project_query
from exemplar.wire.permit import PermitListResponse, PermitView

_PERMITS = TypeAdapter(PermitListResponse)


@dto
@dataclass(frozen=True)
class PermitLines:
    permits: list[PermitView]

    def lines(self) -> Iterator[str]:
        yield f"permits ({len(self.permits)}):"
        for permit in self.permits:
            yield f"  {permit.permit_id}  {permit.holder}  {permit.capability}"


class PermitReader:
    """Reads permits through the command's ``CliContext`` and counts the rows it was handed."""

    def __init__(self, cli: CliContext) -> None:
        self._cli = cli
        self.rows_read = 0

    def show(self, project: str | None) -> None:
        pairs = self._cli.get_all(
            "/api/permits",
            "GET /permits",
            page=_PERMITS,
            rows=lambda page: page.permits,
            key="permits",
            params=project_query(project),
        )
        self.rows_read += len(pairs)
        self._cli.show({"permits": [raw for raw, _ in pairs]}, PermitLines([row for _, row in pairs]))

    def report(self) -> None:
        click.echo(f"read {self.rows_read} permit(s)", err=True)
