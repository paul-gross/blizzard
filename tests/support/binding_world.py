"""A hub holding two projects under a host, and a runner to bind to them (component-tier scaffolding)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.support.hub_request_auth import bearer
from tests.support.project_ingest import ingest_project, two_projects

CAPABILITIES = [{"harness_id": "claude", "default": True}]
REPORT: dict[str, Any] = {
    "name": "box",
    "workspaces": [{"workspace_id": "ws-a", "env_capacity": 2}],
    "subscriptions": [{"slug": "max", "name": "Max", "provider": "anthropic"}],
}


class BindingWorld:
    """A hub holding two projects, each with one ready chunk, and a runner under a host."""

    def __init__(self, tmp_path: Path, *, hosted: bool = True) -> None:
        self.hub, _, _ = two_projects(tmp_path)
        self.chunks: dict[str, str] = {}
        for slug, token in (("default", "a:1"), ("second", "b:1")):
            _, body = ingest_project(self.hub, token)
            self.chunks[slug] = body["chunk_id"]
            assert self.hub.client.post(f"/api/chunks/{body['chunk_id']}/promote").status_code == 202
        self.host = self.hub.client.post("/api/hosts", json={"name": "box"}).json()
        added = self.hub.client.post(
            "/api/runners", json={"name": "r", **({"host_id": self.host["host_id"]} if hosted else {})}
        )
        assert added.status_code == 201, added.text
        self.runner_id = added.json()["runner_id"]
        self.token = added.json()["token"]

    def report(self, workspaces: list[str], subscriptions: list[str] | None = None) -> None:
        body = {
            **REPORT,
            "workspaces": [{"workspace_id": w} for w in workspaces],
            "subscriptions": [
                {"slug": s, "name": s, "provider": "anthropic"}
                for s in (["max"] if subscriptions is None else subscriptions)
            ],
        }
        resp = self.hub.client.post("/api/fleet/host", json=body, headers=bearer(self.host["token"]))
        assert resp.status_code == 200, resp.text

    def register(self, **body: Any) -> dict[str, Any]:
        resp = self.hub.client.post(
            "/api/fleet/runners", json={"workspace_id": "ws-a", **body}, headers=bearer(self.token)
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    def peek(self, **body: Any) -> list[str]:
        resp = self.hub.client.post(
            "/api/fleet/queue/peek", json={"capabilities": CAPABILITIES, **body}, headers=bearer(self.token)
        )
        assert resp.status_code == 200, resp.text
        return [e["chunk_id"] for e in resp.json()["entries"]]


def bindings_of(*pairs: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"project": project, "workspace_id": workspace} for project, workspace in pairs]
