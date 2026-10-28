"""Package-private machinery a runner transfer's runner and host halves share: the copy's handle, the order the
reference declarations put tables in, and the renumbering of integer surrogate keys. It names no table.

A column holding another table's surrogate key declares it (``info={"refers_to": "<table>.<column>"}``), the only map of
references there is: a referred-to table is copied before every table naming it, and each referring value is rewritten
through the keys the destination issued."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy import Column, Connection, Select, Table

from blizzard.runner.transfer.rows import DanglingReference, IRunnerTransferHandle

#: The column a row names its workspace by, renamed when a copy moves a workspace to a new id.
WORKSPACE_COLUMN = "workspace_id"


@dataclass(frozen=True)
class TransferHandle:
    """The concrete handle — the source read and the destination write transaction of one copy, and the surrogate keys
    the destination issued so far (``table -> source key -> destination key``)."""

    runner_id: str
    source: Connection
    destination: Connection
    issued: dict[str, dict[int, int]] = field(default_factory=lambda: dict[str, dict[int, int]]())


def handle_of(handle: IRunnerTransferHandle) -> TransferHandle:
    """Recover the copy from a domain-held handle; only the runner transfer builds one."""
    return cast(TransferHandle, handle)


def references(table: Table) -> dict[str, str]:
    """Each column of ``table`` declaring a reference, mapped to the table it names."""
    return {
        column.name: str(column.info["refers_to"]).partition(".")[0]
        for column in table.columns
        if "refers_to" in column.info
    }


def in_reference_order(tables: Iterable[Table]) -> list[Table]:
    """``tables`` with each referred-to table before every table naming it, ties by name. A reference to a table
    outside ``tables`` imposes nothing: the schema self-test keeps every reference within one kind."""
    pending = {table.name: table for table in tables}
    ordered: list[Table] = []
    while pending:
        ready = sorted(
            name
            for name, table in pending.items()
            if not any(target in pending and target != name for target in references(table).values())
        )
        if not ready:
            raise ValueError(f"the reference declarations among {sorted(pending)} form a cycle")
        for name in ready:
            ordered.append(pending.pop(name))
    return ordered


def surrogate_key(table: Table) -> Column[Any] | None:
    """The integer key the destination issues for ``table``'s rows, or ``None`` for a table keyed otherwise."""
    return table.autoincrement_column


def source_rows(table: Table, select: Select[Any], conn: Connection) -> list[dict[str, Any]]:
    """Every row ``select`` reads from ``table`` on ``conn``, oldest key first, as column-name mappings."""
    key = surrogate_key(table)
    order = [key] if key is not None else list(table.primary_key.columns)
    return [dict(row) for row in conn.execute(select.order_by(*order)).mappings()]


def remapped(table: Table, row: Mapping[str, Any], handle: TransferHandle) -> dict[str, Any]:
    """``row`` with each declared reference rewritten to the key the destination issued for it. A nullable reference
    naming a row the source no longer holds is cleared; a required one raises :class:`DanglingReference`."""
    values = dict(row)
    for name, target in references(table).items():
        old = values[name]
        if old is None:
            continue
        new = handle.issued.get(target, {}).get(old)
        if new is None and not table.c[name].nullable:
            raise DanglingReference(f"{table.name}.{name}", old)
        values[name] = new
    return values
