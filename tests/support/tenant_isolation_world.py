"""Helpers for the tenant isolation tests, shared with other test modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx2

from blizzard.auth_core import Role
from blizzard.hub.domain.cadence.model import CadenceShape, ChoreShape
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.delivery.flows import FlowResource
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.flow_store import FlowStore
from tests.support.scaffolding import (
    HubHarness,
    TenantHarness,
    chunk_unit,
    default_project,
    scoped_connections,
    seed_membership,
    seed_session,
    seed_user,
)

_GRAPH = """
name: {name}
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: done
"""


#: A runner-minted id both tenants' runners post — two rows, never one (MT-STORE-18).
_SHARED_QUESTION = "qn_shared"


_SHARED_SEGMENT = "seg_shared"


@dataclass
class _Tenant:
    """One seeded tenant: what drives it, the value each path parameter takes for its records, and every id and
    name it holds — none of which the other tenant may ever read."""

    harness: TenantHarness
    label: str
    ids: dict[str, str] = field(default_factory=dict[str, str])
    markers: set[str] = field(default_factory=set[str])

    @property
    def client(self):
        return self.harness.client

    def runner(self) -> str:
        return self.harness.runner_id("r1")

    def keep(self, key: str, value: str) -> str:
        self.ids[key] = value
        self.markers.add(value)
        return value


def _ok(response: httpx2.Response, status: int) -> dict[str, Any]:
    assert response.status_code == status, f"{response.request.method} {response.request.url}: {response.text}"
    return response.json() if response.content else {}


def _seed(hub: HubHarness, harness: TenantHarness, label: str) -> _Tenant:
    """Seed one tenant through its own client with one record of every kind a tenant route names, each named
    ``<label>-…`` so the other tenant's reads can be searched for it."""
    t = _Tenant(harness, label)
    c = harness.client
    t.markers.add(t.runner())
    graph = _ok(c.post("/api/graphs", json={"definition_yaml": _GRAPH.format(name=f"{label}-flow")}), 201)
    t.keep("graph_id", graph["graph_id"])
    t.keep("graph_name", f"{label}-flow")
    item = _ok(c.post("/api/work-sources/hub/items", json={"title": f"{label}-item", "body": "b"}), 201)
    t.ids["ref"] = str(item["ref"])
    t.markers.add(f"{label}-item")
    spare = _ok(c.post("/api/work-sources/hub/items", json={"title": f"{label}-spare", "body": "b"}), 201)
    t.ids["spare_token"] = f"hub:{spare['ref']}"
    t.keep("spare_chunk_id", spare["chunk_id"])
    t.ids["hub_source"] = "hub"
    t.ids["sweep"] = "close_drain"
    chunk = t.keep("chunk_id", item["chunk_id"])
    node = _ok(c.get(f"/api/chunks/{chunk}"), 200)["current_node_id"]
    _ok(c.post(f"/api/chunks/{chunk}/promote"), 202)
    claim = _ok(
        c.post(
            "/api/fleet/routes",
            json={"chunk_id": chunk, "runner_id": t.runner(), "workspace_id": "w1", "environment_ids": ["env-a"]},
        ),
        201,
    )
    t.ids["route_token"] = claim["route_token"]
    t.ids["node_id"] = node
    t.ids["runner"] = t.runner()
    t.ids["seq"] = "900"
    lease = {"chunk_id": chunk, "epoch": 1, "route_token": claim["route_token"]}
    own_question = f"qn_{label}"
    facts = [{"seq": 1, "kind": "lease.minted", "payload": lease}] + [
        {
            "seq": seq,
            "kind": "question.asked",
            "payload": {**lease, "question_id": question, "question": f"{label}-question"},
        }
        for seq, question in ((2, _SHARED_QUESTION), (3, own_question))
    ]
    facts.append(
        {
            "seq": 4,
            "kind": "event.recorded",
            "payload": {
                "severity": "warning",
                "kind": "attempt-failed",
                "message": f"{label}-event",
                "chunk_id": chunk,
            },
        }
    )
    ack = _ok(c.post("/api/fleet/events", json={"runner_id": t.runner(), "facts": facts}), 200)
    assert not ack.get("rejected"), ack
    t.keep("question_id", own_question)
    t.markers.add(f"{label}-question")
    t.markers.add(f"{label}-event")
    segment = {
        "seq": 1,
        "segment_id": _SHARED_SEGMENT,
        "chunk_id": chunk,
        "node_id": node,
        "epoch": 1,
        "spawn_generation": 0,
        "turn_range_start": 0,
        "turn_range_end": 0,
        "final": True,
        "normalizer_version": "1",
        "harness_version": "1",
        "turns": [
            {
                "index": 0,
                "role": "assistant",
                "kind": "asst",
                "timestamp": "2026-07-13T00:00:00Z",
                "text": f"{label}-transcript",
                "tool": None,
                "thinking_redacted": False,
                "sidechain": None,
                "truncated": False,
            }
        ],
    }
    _ok(c.post("/api/fleet/transcripts", json={"runner_id": t.runner(), "records": [segment]}), 200)
    t.ids["segment_id"] = _SHARED_SEGMENT
    t.markers.add(f"{label}-transcript")
    t.keep("slug", _ok(c.post("/api/scopes", json={"slug": f"{label}-scope"}), 201)["slug"])
    t.ids["scope_slug"] = t.ids["slug"]
    routine = _ok(
        c.post(
            "/api/routines",
            json={"name": f"{label}-routine", "graph_name": f"{label}-flow", "default_scope_slug": f"{label}-scope"},
        ),
        201,
    )
    t.keep("routine_id", routine["routine_id"])
    t.keep("routine_name", f"{label}-routine")
    # Written through the tenant's own services, so its chore can name the routine created above.
    cadence = harness.services.cadence_authoring.create(
        CadenceShape(
            slug=f"{label}-cadence",
            spacing="1d",
            chores=(ChoreShape(f"{label}-chore", f"{label}-routine", scopes=[f"{label}-scope"]),),
        ),
        project_id=default_project(hub.engine, harness.tenant),
        ctx=ChangeContext(actor=f"{label}-operator", door=Door.API),
        may_admit=True,
    )
    t.keep("cadence_id", cadence.cadence_id)
    t.keep("chore_id", cadence.chores[0].chore_id)
    t.keep("cadence_slug", cadence.slug)
    run = t.keep("run_chunk_id", _ok(c.post(f"/api/routines/{routine['routine_id']}/run", json={}), 201)["chunk_id"])
    _ok(c.post(f"/api/chunks/{run}/promote"), 202)
    t.keep("secret", _ok(c.post("/api/secrets", json={"name": f"{label}-secret", "value": "v"}), 201)["name"])
    repository = {
        "slug": f"{label}-repo",
        "forge_api_url": "https://forge.example",
        "owner": "acme",
        "repo": f"{label}-repo",
        "base_branch": "main",
        "secret_name": f"{label}-secret",
    }
    created_repository = _ok(c.post("/api/repositories", json=repository), 201)
    t.keep("repository", created_repository["repository_id"])
    t.keep("forge", created_repository["forge_id"])
    with chunk_unit(scoped_connections(hub.engine, harness.tenant), chunk) as unit:
        flow = FlowStore(scoped_connections(hub.engine, harness.tenant), hub.clock).submit(
            chunk,
            node_id=node,
            epoch=1,
            generation=0,
            resources=[FlowResource(created_repository["repository_id"], "main")],
            at=hub.clock.now(),
            transaction=unit,
        )
    assert flow is not None
    t.keep("flow_id", flow.flows[0].flow_id)
    t.keep("submission_id", flow.submission_id)
    source = {"slug": f"{label}-source", "provider": "github", "locator": f"acme/{label}", "secret": f"{label}-secret"}
    t.keep("source", _ok(c.post("/api/work-sources", json=source), 201)["work_source_id"])
    FindingStore(scoped_connections(hub.engine, harness.tenant)).add(
        t.keep("finding_id", f"fin_{label}"),
        project_id=default_project(hub.engine, harness.tenant),
        routine_name=f"{label}-routine",
        scope_slug=f"{label}-scope",
        class_=t.keep("finding_class", f"{label}-drift"),
        locus=f"{label}.py:1",
        summary=f"{label}-finding",
        introduced=None,
        at=hub.clock.now(),
    )
    proposal = _ok(
        c.post("/api/garden-proposals", json={"class": "c", "title": f"{label}-proposal", "body": "b"}),
        201,
    )
    t.keep("proposal_id", proposal["proposal_id"])
    answered = _ok(
        c.post(
            "/api/garden-proposals",
            json={"class": "c", "title": f"{label}-answered", "body": "b", "findings": [f"fin_{label}"]},
        ),
        201,
    )
    accepted = _ok(c.post(f"/api/garden-proposals/{answered['proposal_id']}/accept", json={}), 200)
    t.keep("garden_chunk_id", accepted["chunk_id"])
    t.markers.add(f"{label}-answered")
    invitation = _ok(c.post("/api/invitations", json={"email": f"{label}@example.com", "role": "guest"}), 201)
    t.keep("invitation_id", invitation["invitation"]["invitation_id"])
    added = _ok(c.post("/api/runners", json={"name": f"{label}-added"}), 201)
    t.keep("runner_id", added["runner_id"])
    t.keep("host_id", _ok(c.post("/api/hosts", json={"name": f"{label}-box"}), 201)["host_id"])
    member = seed_user(hub, username=f"{label}-member", role=Role.PENDING)
    seed_membership(hub.engine, harness.tenant, member.user_id, Role.GUEST)
    t.keep("user_id", member.user_id)
    _ok(c.post(f"/api/chunks/{accepted['chunk_id']}/promote"), 202)
    # The decision parks the routine run's chunk: the first chunk's open questions refuse a decision there.
    gated = {"chunk_id": run, "runner_id": t.runner(), "workspace_id": "w1", "environment_ids": ["env-b"]}
    run_token = _ok(c.post("/api/fleet/routes", json=gated), 201)["route_token"]
    lease = {"chunk_id": run, "epoch": 1, "route_token": run_token}
    minted = [{"seq": 4, "kind": "lease.minted", "payload": lease}]
    assert not _ok(c.post("/api/fleet/events", json={"runner_id": t.runner(), "facts": minted}), 200).get("rejected")
    at = _ok(c.get(f"/api/chunks/{run}"), 200)["current_node_id"]
    gate = {"runner_id": t.runner(), "from_node_id": at, "epoch": 1, "route_token": run_token}
    parked = _ok(c.post(f"/api/fleet/chunks/{run}/decisions", json=gate), 200)
    decisions = _ok(c.get("/api/decisions"), 200)["decisions"]
    assert len(decisions) == 1, parked
    t.keep("decision_id", decisions[0]["decision_id"])
    # The tenant's one project, under a slug of its own, linked to its source and repository.
    [project] = _ok(c.get("/api/projects"), 200)["projects"]
    t.keep("project", project["project_id"])
    slug = t.keep("project_slug", f"{label}-project")
    _ok(c.patch(f"/api/projects/{project['project_id']}", json={"slug": slug}), 200)
    _ok(c.put(f"/api/projects/{slug}/source-links/{t.ids['source']}"), 200)
    _ok(c.put(f"/api/projects/{slug}/repository-links/{t.ids['repository']}"), 200)
    return t


@dataclass(frozen=True)
class Two:
    hub: HubHarness
    first: _Tenant
    second: _Tenant

    def both(self) -> tuple[_Tenant, _Tenant]:
        return (self.first, self.second)


def seeded(hub: HubHarness, *labels: str) -> list[_Tenant]:
    """Tenants named ``labels`` on ``hub``, seeded alike, each driven by one person who administers every one of
    them as a member and holds no hub-level role."""
    person = seed_user(hub, username="operator", role=Role.PENDING)
    session = seed_session(hub, person)
    tenants: list[_Tenant] = []
    for label in labels:
        harness = hub.add_tenant(label)
        seed_membership(hub.engine, harness.tenant, person.user_id, Role.ADMIN)
        harness.client.headers["Cookie"] = f"bz_session={session}"
        tenants.append(_seed(hub, harness, label))
    return tenants
