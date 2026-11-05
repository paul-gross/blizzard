"""Names the unit and component ``test_init_tenant`` modules share."""

from __future__ import annotations

from blizzard.runner.hub.client import OperatorTenant

_ALPHA = OperatorTenant(tenant_id="ten_alpha", name="alpha")

_BRAVO = OperatorTenant(tenant_id="ten_bravo", name="bravo")
