"""Tenant teardown at service size — a tenant holding what a service test creates (its packaged graphs, a handful of
chunks ingested and promoted through the API, and their facts) deletes inline on SQLite well under a second.

The time is measured and reported, not held to a tight bound: the bound asserted is loose enough that only a drain
gone pathological — a table walked row by row, a batch that never shrinks — fails it."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from blizzard.hub.domain.tenancy.teardown import DeletionState
from tests.service.support import service_gate
from tests.support import build_hub, default_tenant, ingest

pytestmark = [pytest.mark.service, service_gate]

#: Chunks the measured tenant holds — the handful a service test ingests.
_CHUNKS = 8

#: A bound only a pathological drain exceeds; the measurement itself is reported.
_LOOSE_BOUND_SECONDS = 5.0


def test_a_service_sized_tenant_tears_down_well_under_a_second(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    measured = default_tenant(hub.engine)
    for n in range(_CHUNKS):
        ingest(hub, [{"source": "default", "ref": str(n)}])
    hub.hub_services.tenant_admin.create("survivor", admin_user_id=None, by="usr_creator")

    started = time.perf_counter()
    progress = hub.hub_services.tenant_teardown.delete(measured, by="usr_closer")
    elapsed = time.perf_counter() - started

    print(f"\ntenant teardown: {progress.rows_removed} rows in {elapsed * 1000:.1f} ms")
    assert progress.state is DeletionState.DELETED
    assert progress.rows_removed is not None and progress.rows_removed > _CHUNKS
    assert elapsed < _LOOSE_BOUND_SECONDS
