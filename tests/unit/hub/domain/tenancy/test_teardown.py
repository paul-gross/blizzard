"""The last-open-tenant refusal (unit tier), pinned by value over the tenants a close would leave open."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.tenancy.teardown import LastTenant, require_not_last
from blizzard.hub.tenancy import TenantId

_ALPHA = TenantId("alpha")
_BETA = TenantId("beta")


def test_closing_the_only_open_tenant_is_refused() -> None:
    with pytest.raises(LastTenant) as refusal:
        require_not_last(_ALPHA, [_ALPHA])

    assert refusal.value.tenant == _ALPHA


def test_closing_a_tenant_while_another_is_open_is_not_refused() -> None:
    require_not_last(_ALPHA, [_ALPHA, _BETA])


def test_closing_a_tenant_when_nothing_is_open_is_refused() -> None:
    with pytest.raises(LastTenant):
        require_not_last(_ALPHA, [])
