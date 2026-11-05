"""The host's identity at its hub: the store's single ``host_identity`` row, written once and never edited, and the
process holder every consumer reads."""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard.runner.hub.host_identity import HostIdentity

_AT = datetime(2026, 10, 1, 12, tzinfo=UTC)


_IDENTITY = HostIdentity(host_id="hs_1", tenant_id="tn_1", registered_at=_AT)
