"""Constants the unit and component halves of the sweep roster tests share."""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard.hub.tenancy import TenantId

A = TenantId("ten_a")


B = TenantId("ten_b")


NOW = datetime(2026, 7, 13, tzinfo=UTC)
