"""The hub's in-memory open-tenant set: a load that read the tenants before a change committed never
replaces the copy the change's own refresh read after it."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping

import pytest

from blizzard.hub.open_tenants import OpenTenants
from blizzard.hub.tenancy import TenantId

pytestmark = pytest.mark.unit

_ACME = TenantId("ten_acme")
_BRAVO = TenantId("ten_bravo")
_BOGUS = TenantId("ten_bogus")

# How long the post-commit refresh is given to finish while the earlier load is still blocked.
_OVERTAKE_WINDOW_S = 0.3
_JOIN_S = 5.0


class _BlockingTable:
    """The tenants table; once armed, its next load blocks after its read until released."""

    def __init__(self, rows: dict[TenantId, str]) -> None:
        self.rows = rows
        self.read_done = threading.Event()
        self.release = threading.Event()
        self.later_load_ran = threading.Event()
        self.armed = False

    def load(self) -> Mapping[TenantId, str]:
        read = dict(self.rows)
        if self.armed:
            self.armed = False
            self.read_done.set()
            assert self.release.wait(_JOIN_S)
        elif self.read_done.is_set():
            self.later_load_ran.set()
        return read


def _rename(rows: dict[TenantId, str]) -> None:
    rows[_ACME] = "acme-two"


def _create(rows: dict[TenantId, str]) -> None:
    rows[_BRAVO] = "bravo"


def _close(rows: dict[TenantId, str]) -> None:
    del rows[_ACME]


@pytest.mark.parametrize("change", [_rename, _create, _close], ids=["rename", "create", "close"])
def test_a_load_started_before_a_change_never_replaces_the_changes_refresh(
    change: Callable[[dict[TenantId, str]], None],
) -> None:
    table = _BlockingTable({_ACME: "acme", TenantId("ten_other"): "other"})
    tenants = OpenTenants(table.load)
    tenants.every()
    table.armed = True
    unknown = threading.Thread(target=tenants.name, args=(_BOGUS,))
    unknown.start()
    assert table.read_done.wait(_JOIN_S)

    change(table.rows)
    after_commit = threading.Thread(target=tenants.refresh)
    after_commit.start()
    table.later_load_ran.wait(_OVERTAKE_WINDOW_S)
    table.release.set()
    unknown.join(_JOIN_S)
    after_commit.join(_JOIN_S)

    assert tenants.every() == table.rows
