"""Two tenants on one hub, at the store (component tier): every scoped adapter keeps each tenant's rows its own.

Both tenants are seeded alike through the API (every record kind a tenant route names), so each tenant-owned table
holds rows of both. Then every row a tenant wrote carries that tenant's id, and every adapter a tenant's stores are
built from reads back none of the other tenant's records.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from blizzard.hub.composition import TenantStores, build_hub_core
from blizzard.hub.store import schema
from blizzard.hub.tenancy import StoreScope
from tests.support import build_hub, hub_scoped
from tests.test_tenant_isolation import _seeded, _Two

pytestmark = pytest.mark.component


@pytest.fixture
def two(tmp_path: Path) -> _Two:
    hub = build_hub(tmp_path, auth_mode="oauth")
    return _Two(hub, *_seeded(hub, "alpha", "beta"))


#: The reads an adapter offers with no argument: every listing a tenant's stores answer whole.
_READ_PREFIXES = ("list", "load", "count", "all", "live", "pending", "read", "latest", "enabled", "known")


def _tenant_owned() -> list[sa.Table]:
    return [table for table in schema.metadata.sorted_tables if table.name not in schema.GLOBAL_TABLES]


def _text_of(row: sa.Row[Any]) -> str:
    return " ".join(str(value) for value in row if isinstance(value, str))


def test_every_row_a_tenant_wrote_carries_that_tenant(two: _Two) -> None:
    written: dict[str, set[str]] = {}
    with two.hub.engine.connect() as conn:
        for table in _tenant_owned():
            for row in conn.execute(hub_scoped(sa.select(table))):
                text = _text_of(row)
                for t in two.both():
                    if any(marker in text for marker in t.markers if len(marker) > 6):
                        written.setdefault(table.name, set()).add(t.label)
                        assert row.tenant_id == t.harness.tenant, f"{table.name} holds {t.label}'s row elsewhere"
    both = sorted(name for name, labels in written.items() if labels == {"alpha", "beta"})
    assert len(both) >= 15, both


def _reads_of(adapter: object) -> Iterator[tuple[str, object]]:
    """Each argument-free read ``adapter`` offers, called."""
    for name, method in inspect.getmembers(adapter, inspect.ismethod):
        if name.startswith("_") or not name.startswith(_READ_PREFIXES):
            continue
        parameters = inspect.signature(method).parameters.values()
        if any(p.default is inspect.Parameter.empty and p.kind is not p.VAR_KEYWORD for p in parameters):
            continue
        yield name, method()


def _adapters(stores: TenantStores) -> Iterator[tuple[str, object]]:
    for field in dataclasses.fields(stores):
        value = getattr(stores, field.name)
        yield field.name, value
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            for inner in dataclasses.fields(value):
                yield f"{field.name}.{inner.name}", getattr(value, inner.name)


def test_each_tenants_adapters_read_back_none_of_the_others_records(two: _Two) -> None:
    core = build_hub_core(two.hub.engine, clock=two.hub.clock)
    called: set[str] = set()
    for reader, other in ((two.first, two.second), (two.second, two.first)):
        stores = core.open(StoreScope(reader.harness.tenant))
        for adapter_name, adapter in _adapters(stores):
            for read, answer in _reads_of(adapter):
                text = repr(answer)
                called.add(f"{adapter_name}.{read}")
                leaked = {marker for marker in other.markers if len(marker) > 6 and marker in text}
                assert not leaked, f"{reader.label}'s {adapter_name}.{read} reads {other.label}'s {sorted(leaked)}"
    assert len(called) >= 15, sorted(called)
