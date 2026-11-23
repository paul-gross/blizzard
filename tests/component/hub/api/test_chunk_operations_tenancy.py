"""The chunk reads and operations act in the tenant the request admits: a tenant's client neither sees another
tenant's chunk nor changes it (component tier)."""

from __future__ import annotations

from pathlib import Path

from blizzard.hub.api.auth_session import TENANT_HEADER
from tests.support.scaffolding import build_hub, default_tenant, ingest


def test_a_tenant_neither_reads_nor_operates_on_another_tenants_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    theirs = ingest(hub, [{"source": "default", "ref": "1"}])
    before = hub.client.get(f"/api/chunks/{theirs}").json()
    other = hub.add_tenant("other")

    assert other.client.get("/api/chunks").json()["chunks"] == []
    assert other.client.get("/api/queue").json()["entries"] == []
    assert other.client.get(f"/api/chunks/{theirs}").status_code == 404
    assert other.client.get(f"/api/chunks/{theirs}/work-items").status_code == 404
    assert other.client.post(f"/api/chunks/{theirs}/pause", json={"by": "other"}).status_code == 404
    assert other.client.post(f"/api/chunks/{theirs}/demote").status_code == 404
    assert other.client.request("DELETE", f"/api/chunks/{theirs}", json={}).status_code == 404

    named = {TENANT_HEADER: default_tenant(hub.engine)}
    after = hub.client.get(f"/api/chunks/{theirs}", headers=named).json()
    assert (after["status"], after.get("pause")) == (before["status"], before.get("pause"))
    assert after.get("pause") is None
    assert [entry["chunk_id"] for entry in hub.client.get("/api/queue", headers=named).json()["entries"]] == [theirs]
