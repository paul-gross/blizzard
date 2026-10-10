"""Two tenants on one hub, through the API (component tier).

Every route a tenant reaches — the human plane's tenant routes and the fleet plane — is named in one table with
the probe that proves its isolation, and the table is checked against the live app both ways. Two tenants are
seeded with the same shape of records under names that never repeat across them; then:

- a listing in either tenant names none of the other's records;
- a request in one tenant naming the other's record — by path or in its body — answers as for a record that does
  not exist, and changes nothing the other tenant reads;
- the same request sent by the record's own tenant answers otherwise than one naming a record never minted, so no
  such probe is blind to a leak;
- a write in one tenant lands in that tenant only: the other tenant's every read answers byte for byte as before;
- each tenant's live stream replays its own events only;
- a runner-minted id posted in both tenants is two rows, each answered on its own (MT-STORE-18);
- a route token bound to one tenant's chunk acts in that chunk's tenant only (MT-DEP-04);
- the packaged system artifacts read the same from either tenant, from no tenant's state (MT-GLOBAL-05);
- the hub-level export routes refuse a tenant member (MT-COMPAT-07).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI

from blizzard.auth_core import Role
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.hub.app import Sweep
from blizzard.hub.composition import TenantStores, build_hub_core
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.domain.projects.authoring import ProjectAuthoring
from blizzard.hub.domain.projects.model import ProjectEdit, ProjectId
from blizzard.hub.store import schema
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.tenancy import StoreScope
from tests.support import (
    OP,
    HubHarness,
    InMemoryTraceExporter,
    TenantHarness,
    build_hub,
    capture_statements,
    close_tenant,
    default_project,
    drain_stream,
    hub_scoped,
    runner_token,
    scoped_connections,
    seed_membership,
    seed_session,
    seed_user,
)
from tests.test_route_classification import _FLEET, _FLEET_CLAIM, _HUB_LEVEL, _HUMAN, _live_routes
from tests.test_sweeps_per_tenant import _close_events, _landed_hub_ref

pytestmark = pytest.mark.component

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
    ids: dict[str, str] = field(default_factory=dict)
    markers: set[str] = field(default_factory=set)

    @property
    def client(self):  # type: ignore[no-untyped-def]
        return self.harness.client

    def runner(self) -> str:
        return self.harness.runner_id("r1")

    def keep(self, key: str, value: str) -> str:
        self.ids[key] = value
        self.markers.add(value)
        return value


Kind = Literal["listing", "foreign", "own", "stream", "global"]


@dataclass(frozen=True)
class _Probe:
    """How one route's isolation is proven.

    - ``listing``: read in each tenant; the body names none of the other tenant's records.
    - ``foreign``: sent in the second tenant naming the first tenant's records — its path parameters, and
      ``{placeholders}`` in ``body`` — it answers ``status``, and exactly what the same request naming records that
      were never minted answers (the two are indistinguishable).
    - ``own``: a write in the second tenant naming only its own records; answers ``status``.
    - ``stream``: the live stream — each tenant's replay tail holds its own events only.
    - ``global``: serves no tenant's state — the same answer in every tenant.

    Every ``foreign`` and ``own`` request runs between two snapshots of the first tenant's reads, which must agree;
    and each ``foreign`` request, sent by the record's own tenant, answers otherwise than one naming records never
    minted — a probe whose record cannot tell itself from a ghost could never see a leak.
    ``reason`` says why a probe answers other than the plain ``404`` (``foreign``) or ``2xx`` (``own``); ``ids``
    names the seeded record a path parameter or body placeholder takes when it is not the one of the same name.
    """

    kind: Kind
    body: object = None
    status: int = 404
    params: dict[str, str] | None = None
    reason: str = ""
    ids: dict[str, str] | None = None


def _f(
    body: object = None,
    *,
    status: int = 404,
    params: dict[str, str] | None = None,
    reason: str = "",
    ids: dict[str, str] | None = None,
) -> _Probe:
    return _Probe("foreign", body, status, params, reason, ids)


def _own(body: object = None, *, status: int = 200, reason: str = "", ids: dict[str, str] | None = None) -> _Probe:
    return _Probe("own", body, status, None, reason, ids)


def _list(**params: str) -> _Probe:
    return _Probe("listing", params=params or None)


#: A hub-executed node's marker coordinates; a request naming another tenant's chunk never reaches them.
_MARKER = {"node_id": "nd_x", "epoch": "1"}
_WINDOW = {"since": "2026-01-01T00:00:00Z", "until": "2026-12-31T00:00:00Z"}
_SINCE = {"since": "2026-01-01T00:00:00Z"}
_SEGMENT = {"node_id": "{node_id}", "epoch": "1"}
_NOT_THE_BEARER = "a runner acts as itself: a path naming any other runner is refused before it is looked up"
_UNLISTED = "a list edit names chunk ids: a chunk outside this tenant's list answers as one that is in no list"
#: A routine run's chunk: the run-context reads answer only for one.
_RUN = {"chunk_id": "run_chunk_id"}
#: A resting chunk: the backlog's list edits name one.
_BACKLOG = {"chunk_id": "spare_chunk_id"}
#: The chunk an accepted garden proposal minted: the answered-findings reads answer only for one. It is promoted, so
#: the queue's list edits name it too.
_GARDEN = {"chunk_id": "garden_chunk_id"}
#: The built-in ``hub`` source — the only one with an editor — and an item at a ref the other tenant never allocated.
_ITEMS = {"source": "hub_source"}
_LIST = _Probe("listing")
_EXIT = {"finding_ids": ["{finding_id}"], "note": "n"}

#: Every tenant route — ``(method, path) -> probe``. Exhaustive both ways against the live app.
_PROBES: dict[tuple[str, str], _Probe] = {
    # -- events, activity, analytics -------------------------------------------------------------------------
    ("GET", "/api/events/stream"): _Probe("stream"),
    ("GET", "/api/events"): _LIST,
    ("GET", "/api/activity"): _LIST,
    ("GET", "/api/analytics/counts/agent-types"): _LIST,
    ("GET", "/api/analytics/counts/files"): _LIST,
    ("GET", "/api/analytics/counts/nodes"): _LIST,
    ("GET", "/api/analytics/counts/skills"): _LIST,
    ("GET", "/api/analytics/durations/graphs"): _LIST,
    ("GET", "/api/analytics/durations/nodes"): _LIST,
    ("GET", "/api/analytics/events"): _LIST,
    ("GET", "/api/analytics/events/ndjson"): _LIST,
    ("GET", "/api/analytics/outcomes/nodes"): _LIST,
    ("GET", "/api/analytics/spend/chunks"): _LIST,
    ("GET", "/api/analytics/spend/chunks/ndjson"): _LIST,
    ("GET", "/api/analytics/spend/graphs"): _LIST,
    ("GET", "/api/analytics/spend/nodes"): _LIST,
    ("POST", "/api/analytics/re-derive"): _own({}),
    ("GET", "/api/spend"): _list(**_SINCE),
    # -- backlog and queue -----------------------------------------------------------------------------------
    ("GET", "/api/backlog"): _LIST,
    ("PUT", "/api/backlog"): _f({"chunk_ids": ["{chunk_id}"]}, status=409, reason=_UNLISTED, ids=_BACKLOG),
    ("POST", "/api/backlog/position"): _f(
        {"chunk_id": "{chunk_id}", "after_chunk_id": None}, status=409, reason=_UNLISTED, ids=_BACKLOG
    ),
    ("GET", "/api/queue"): _LIST,
    ("PUT", "/api/queue"): _f({"chunk_ids": ["{chunk_id}"]}, status=409, reason=_UNLISTED, ids=_GARDEN),
    ("POST", "/api/queue/position"): _f(
        {"chunk_id": "{chunk_id}", "after_chunk_id": None}, status=409, reason=_UNLISTED, ids=_GARDEN
    ),
    ("GET", "/api/chunk-counts"): _LIST,
    # -- chunks ----------------------------------------------------------------------------------------------
    ("POST", "/api/chunks"): _own(
        {"tokens": ["{spare_token}"]},
        status=409,
        reason="a hub item is ingested when created: the conflict names the second tenant's own chunk",
    ),
    ("GET", "/api/chunks"): _LIST,
    ("GET", "/api/chunks/{chunk_id}"): _f(),
    ("PATCH", "/api/chunks/{chunk_id}"): _f({}),
    ("DELETE", "/api/chunks/{chunk_id}"): _f({}),
    ("POST", "/api/chunks/{chunk_id}/complete"): _f({}),
    ("POST", "/api/chunks/{chunk_id}/dependencies"): _f({"prerequisite_chunk_id": "{chunk_id}"}),
    ("POST", "/api/chunks/{chunk_id}/dependencies/release"): _f({"prerequisite_chunk_id": "{chunk_id}"}),
    ("POST", "/api/chunks/{chunk_id}/detach"): _f(),
    ("POST", "/api/chunks/{chunk_id}/garden-delivery"): _f({}, params=_MARKER),
    ("POST", "/api/chunks/{chunk_id}/group"): _f({"merge_chunk_ids": ["{chunk_id}"]}),
    ("POST", "/api/chunks/{chunk_id}/hub-markers"): _f({"name": "m"}, params=_MARKER),
    ("POST", "/api/chunks/{chunk_id}/pause"): _f({}),
    ("GET", "/api/chunks/{chunk_id}/pm-items"): _f(),
    ("POST", "/api/chunks/{chunk_id}/promote"): _f(),
    ("POST", "/api/chunks/{chunk_id}/requeues"): _f(),
    ("POST", "/api/chunks/{chunk_id}/restart"): _f({}),
    ("POST", "/api/chunks/{chunk_id}/resume"): _f({}),
    ("POST", "/api/chunks/{chunk_id}/review-findings-delivery"): _f(params=_MARKER),
    ("POST", "/api/chunks/{chunk_id}/stop"): _f({}),
    ("GET", "/api/chunks/{chunk_id}/transcripts"): _f(),
    ("GET", "/api/chunks/{chunk_id}/transcripts/{segment_id}"): _f(),
    ("GET", "/api/chunks/{chunk_id}/work-items"): _f(),
    ("GET", "/api/runs"): _LIST,
    ("GET", "/api/runs/{chunk_id}"): _f(ids=_RUN),
    # -- configuration ---------------------------------------------------------------------------------------
    ("POST", "/api/config/apply"): _own(),
    ("GET", "/api/config/changes"): _LIST,
    ("GET", "/api/config/export"): _LIST,
    ("GET", "/api/config/schema/{kind}"): _Probe("global", reason="a document kind's JSON schema, from the code"),
    # -- decisions, questions --------------------------------------------------------------------------------
    ("GET", "/api/decisions"): _LIST,
    ("POST", "/api/decisions/{decision_id}/resolutions"): _f({"choice": "pass"}),
    ("GET", "/api/questions"): _LIST,
    ("POST", "/api/questions"): _f(
        {
            "question_id": "qn_foreign",
            "chunk_id": "{chunk_id}",
            "runner_id": "{runner}",
            "epoch": 1,
            "question": "q",
            "asked_at": "2026-07-13T00:00:00Z",
        }
    ),
    ("POST", "/api/questions/{question_id}/answers"): _f({"answer": "a"}),
    # -- findings and garden proposals -----------------------------------------------------------------------
    ("GET", "/api/findings"): _LIST,
    ("GET", "/api/findings/{finding_id}"): _f(),
    ("POST", "/api/findings/confirm-gone"): _f(_EXIT),
    ("POST", "/api/findings/not-a-finding"): _f(_EXIT),
    ("POST", "/api/findings/reopen"): _f(_EXIT),
    ("POST", "/api/findings/resolve"): _f(_EXIT),
    ("POST", "/api/findings/wont-fix"): _f(_EXIT),
    ("POST", "/api/findings/supersede"): _f({**_EXIT, "superseded_by": "{finding_id}"}),
    ("GET", "/api/garden-proposals"): _LIST,
    ("POST", "/api/garden-proposals"): _f(
        {"class": "c", "title": "t", "body": "b", "findings": ["{finding_id}"]},
        status=422,
        reason="the body names findings: one outside the tenant is an unknown finding id",
    ),
    ("GET", "/api/garden-proposals/{proposal_id}"): _f(),
    ("PATCH", "/api/garden-proposals/{proposal_id}"): _f({}),
    ("POST", "/api/garden-proposals/{proposal_id}/accept"): _f({}),
    ("POST", "/api/garden-proposals/{proposal_id}/attach"): _f({"findings": []}),
    ("POST", "/api/garden-proposals/{proposal_id}/detach"): _f({"findings": []}),
    ("POST", "/api/garden-proposals/{proposal_id}/pass"): _f({"reason": "r"}),
    # -- graphs ----------------------------------------------------------------------------------------------
    ("POST", "/api/graphs"): _own({"definition_yaml": _GRAPH.format(name="beta-extra")}, status=201),
    ("GET", "/api/graphs"): _LIST,
    ("POST", "/api/graphs/sync"): _own(),
    ("GET", "/api/graphs/{graph_id}"): _f(),
    ("PATCH", "/api/graphs/{graph_id}"): _f({}),
    ("POST", "/api/graphs/{graph_id}/enable"): _f(),
    ("POST", "/api/graphs/{graph_id}/follow-latest"): _f({"follow_latest": True}),
    ("POST", "/api/graphs/{graph_id}/retire"): _f(),
    # -- people ----------------------------------------------------------------------------------------------
    ("POST", "/api/invitations"): _own({"email": "beta-extra@example.com", "role": "guest"}, status=201),
    ("GET", "/api/invitations"): _LIST,
    ("POST", "/api/invitations/{invitation_id}/revocations"): _f(),
    ("GET", "/api/members"): _LIST,
    ("PUT", "/api/members/{user_id}"): _f({"role": "admin"}),
    ("DELETE", "/api/members/{user_id}"): _f(),
    ("GET", "/api/users"): _LIST,
    ("POST", "/api/users/{user_id}/role"): _f({"role": "admin"}),
    # -- repositories, secrets, work sources -----------------------------------------------------------------
    ("GET", "/api/repositories"): _LIST,
    ("POST", "/api/repositories"): _own(
        {
            "name": "beta-extra",
            "forge_api_url": "https://forge.example",
            "owner": "acme",
            "repo": "beta-extra",
            "base_branch": "main",
            "secret_name": "{secret}",
        },
        status=201,
    ),
    ("GET", "/api/repositories/{name}"): _f(),
    ("PATCH", "/api/repositories/{name}"): _f({}),
    ("POST", "/api/repositories/{name}/enable"): _f(),
    ("POST", "/api/repositories/{name}/retire"): _f(),
    ("POST", "/api/secrets"): _own({"name": "beta-extra", "value": "v"}, status=201),
    ("GET", "/api/secrets"): _LIST,
    ("GET", "/api/secrets/{name}"): _f(),
    ("POST", "/api/secrets/{name}/enable"): _f(),
    ("POST", "/api/secrets/{name}/retire"): _f(),
    ("PUT", "/api/secrets/{name}/value"): _f({"value": "stolen"}),
    ("GET", "/api/work-sources"): _LIST,
    ("POST", "/api/work-sources"): _own(
        {"name": "beta-extra", "provider": "github", "locator": "acme/beta-extra", "secret": "{secret}"}, status=201
    ),
    ("GET", "/api/work-sources/{source}"): _f(),
    ("PATCH", "/api/work-sources/{source}"): _f({}),
    ("POST", "/api/work-sources/{source}/enable"): _f(),
    ("POST", "/api/work-sources/{source}/retire"): _f(),
    ("GET", "/api/work-sources/{source}/items"): _Probe("listing", ids=_ITEMS),
    ("POST", "/api/work-sources/{source}/items"): _own({"title": "beta-extra", "body": "b"}, status=201, ids=_ITEMS),
    ("GET", "/api/work-sources/{source}/items/{ref}"): _f(ids=_ITEMS),
    ("PATCH", "/api/work-sources/{source}/items/{ref}"): _f({}, ids=_ITEMS),
    ("DELETE", "/api/work-sources/{source}/items/{ref}"): _f(ids=_ITEMS),
    # -- routines and scopes ---------------------------------------------------------------------------------
    ("POST", "/api/routines"): _f(
        {"name": "beta-extra", "graph_name": "{graph_name}", "default_scope_slug": "s"},
        status=422,
        reason="the body names a graph: one outside the tenant is no enabled graph of that name",
    ),
    ("GET", "/api/routines"): _LIST,
    ("GET", "/api/routines/proposal-counts"): _list(**_WINDOW),
    ("GET", "/api/routines/trend"): _f(
        params={"routine": "{routine_name}", "introduced_boundary": "2026-06-01T00:00:00Z", **_WINDOW}
    ),
    ("GET", "/api/routines/{routine_id}"): _f(),
    ("PATCH", "/api/routines/{routine_id}"): _f({}),
    ("GET", "/api/routines/{routine_id}/baselines"): _f(),
    ("POST", "/api/routines/{routine_id}/enable"): _f({}),
    ("POST", "/api/routines/{routine_id}/retire"): _f({}),
    ("POST", "/api/routines/{routine_id}/run"): _f({}),
    ("GET", "/api/routines/{routine_id}/scopes"): _f(),
    ("PUT", "/api/routines/{routine_id}/scopes/{scope_slug}"): _f(),
    ("DELETE", "/api/routines/{routine_id}/scopes/{scope_slug}"): _f(),
    ("GET", "/api/routines/{routine_id}/sweeps"): _f(params=_WINDOW),
    ("POST", "/api/scopes"): _own({"slug": "beta-extra"}, status=201),
    ("GET", "/api/scopes"): _LIST,
    ("GET", "/api/scopes/{slug}"): _f(),
    ("PATCH", "/api/scopes/{slug}"): _f({}),
    ("POST", "/api/scopes/{slug}/enable"): _f({}),
    ("POST", "/api/scopes/{slug}/retire"): _f({}),
    ("GET", "/api/scopes/{slug}/routines"): _f(),
    # -- runners (people) ------------------------------------------------------------------------------------
    ("POST", "/api/runners"): _own({"name": "beta-extra"}, status=201),
    ("GET", "/api/runners"): _LIST,
    ("GET", "/api/runners/{runner_id}"): _f(),
    ("POST", "/api/runners/{runner_id}/enrollments"): _f(),
    ("POST", "/api/runners/{runner_id}/pause"): _f({}),
    ("POST", "/api/runners/{runner_id}/reinstate"): _f({}),
    ("POST", "/api/runners/{runner_id}/resume"): _f({}),
    ("POST", "/api/runners/{runner_id}/retire"): _f({}),
    ("POST", "/api/runners/{runner_id}/token-revocations"): _f({}),
    # -- the fleet plane: the second tenant's runner, the first tenant's records -----------------------------
    ("GET", "/api/fleet/chunk-statuses"): _f(
        status=200,
        params={"chunk_id": "{chunk_id}"},
        reason="answers the statuses of the named chunks it holds: another tenant's chunk is left out as unknown",
    ),
    ("GET", "/api/fleet/chunks/{chunk_id}"): _f(),
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/counts/agent-types"): _f(params=_SINCE, ids=_RUN),
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/counts/files"): _f(params=_SINCE, ids=_RUN),
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/counts/nodes"): _f(params=_SINCE, ids=_RUN),
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/counts/skills"): _f(params=_SINCE, ids=_RUN),
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/spend/graphs"): _f(params=_SINCE, ids=_RUN),
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/spend/nodes"): _f(params=_SINCE, ids=_RUN),
    ("POST", "/api/fleet/chunks/{chunk_id}/completions"): _f(
        {"choice": "pass", "epoch": 1, "from_node_id": "build", "route_token": "{route_token}"}
    ),
    ("POST", "/api/fleet/chunks/{chunk_id}/decisions"): _f(
        {"from_node_id": "build", "epoch": 1, "route_token": "{route_token}"}
    ),
    ("GET", "/api/fleet/chunks/{chunk_id}/envelope"): _f(),
    ("GET", "/api/fleet/chunks/{chunk_id}/findings"): _f(ids=_GARDEN),
    ("GET", "/api/fleet/chunks/{chunk_id}/findings/{finding_id}"): _f(ids=_GARDEN),
    ("GET", "/api/fleet/chunks/{chunk_id}/garden/findings"): _f(ids=_RUN),
    ("GET", "/api/fleet/chunks/{chunk_id}/garden/proposals"): _f(ids=_RUN),
    ("POST", "/api/fleet/chunks/{chunk_id}/hub-advance"): _f(),
    ("POST", "/api/fleet/chunks/{chunk_id}/pause"): _f(),
    ("GET", "/api/fleet/chunks/{chunk_id}/pm-items"): _f(),
    ("POST", "/api/fleet/chunks/{chunk_id}/resume"): _f(),
    ("POST", "/api/fleet/chunks/{chunk_id}/route-token"): _f(),
    ("GET", "/api/fleet/chunks/{chunk_id}/transcript-segments"): _f(
        status=200, params=_SEGMENT, reason="segments are read by their key: an unknown chunk's read is empty"
    ),
    ("GET", "/api/fleet/chunks/{chunk_id}/work-items"): _f(),
    ("POST", "/api/fleet/events"): _f(
        {
            "facts": [
                {
                    "seq": 900,
                    "kind": "lease.minted",
                    "payload": {"chunk_id": "{chunk_id}", "epoch": 2, "route_token": "{route_token}"},
                }
            ]
        },
        status=200,
        reason="a batch acks per fact: the foreign fact is rejected, nothing is applied",
    ),
    ("GET", "/api/fleet/questions/{question_id}"): _f(),
    ("GET", "/api/fleet/queue/peek"): _LIST,
    ("POST", "/api/fleet/queue/peek"): _own({}),
    ("POST", "/api/fleet/routes"): _f({"chunk_id": "{chunk_id}", "workspace_id": "w1", "environment_ids": ["env-a"]}),
    ("POST", "/api/fleet/runners"): _own({"workspace_id": "w2"}, status=201),
    ("GET", "/api/fleet/runners/{runner_id}"): _f(status=403, reason=_NOT_THE_BEARER),
    ("POST", "/api/fleet/runners/{runner_id}/heartbeats"): _f(status=403, reason=_NOT_THE_BEARER),
    ("GET", "/api/fleet/scopes"): _LIST,
    ("GET", "/api/fleet/summary"): _LIST,
    ("GET", "/api/fleet/system-artifacts"): _Probe("global", reason="the installed wheel's packaged documents"),
    ("GET", "/api/fleet/system-artifacts/{name:path}"): _Probe("global", reason="one packaged document"),
    ("POST", "/api/fleet/transcripts"): _f(
        {
            "records": [
                {
                    "seq": "{seq}",
                    "segment_id": "seg_foreign",
                    "chunk_id": "{chunk_id}",
                    "node_id": "build",
                    "epoch": 1,
                    "spawn_generation": 0,
                    "turn_range_start": 0,
                    "turn_range_end": 0,
                    "final": True,
                    "normalizer_version": "1",
                    "harness_version": "1",
                    "turns": [],
                }
            ]
        },
        status=200,
        reason="a batch acks per record, stored under the sender's tenant whatever chunk it names",
    ),
}


def test_the_table_names_every_live_tenant_route_and_nothing_else(tmp_path: Path) -> None:
    app = build_hub(tmp_path).client.app
    assert isinstance(app, FastAPI)
    tenant_routes = (set(_HUMAN) - _HUB_LEVEL) | _FLEET | {_FLEET_CLAIM}

    assert tenant_routes <= _live_routes(app)
    assert set(_PROBES) == tenant_routes, (
        f"unprobed: {sorted(tenant_routes - set(_PROBES))}; not a tenant route: {sorted(set(_PROBES) - tenant_routes)}"
    )


# --- seeding ------------------------------------------------------------------------------------------------------


def _ok(response, status: int) -> dict:  # type: ignore[no-untyped-def]
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
    ack = _ok(c.post("/api/fleet/events", json={"runner_id": t.runner(), "facts": facts}), 200)
    assert not ack.get("rejected"), ack
    t.keep("question_id", own_question)
    t.markers.add(f"{label}-question")
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
    run = t.keep("run_chunk_id", _ok(c.post(f"/api/routines/{routine['routine_id']}/run", json={}), 201)["chunk_id"])
    _ok(c.post(f"/api/chunks/{run}/promote"), 202)
    t.keep("secret", _ok(c.post("/api/secrets", json={"name": f"{label}-secret", "value": "v"}), 201)["name"])
    repository = {
        "name": f"{label}-repo",
        "forge_api_url": "https://forge.example",
        "owner": "acme",
        "repo": f"{label}-repo",
        "base_branch": "main",
        "secret_name": f"{label}-secret",
    }
    t.keep("repository", _ok(c.post("/api/repositories", json=repository), 201)["name"])
    source = {"name": f"{label}-source", "provider": "github", "locator": f"acme/{label}", "secret": f"{label}-secret"}
    t.keep("source", _ok(c.post("/api/work-sources", json=source), 201)["name"])
    FindingStore(scoped_connections(hub.engine, harness.tenant)).add(
        t.keep("finding_id", f"fin_{label}"),
        project_id=default_project(hub.engine, harness.tenant),
        routine_name=f"{label}-routine",
        scope_slug=f"{label}-scope",
        class_="stale-docstring",
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
    return t


@dataclass(frozen=True)
class _Two:
    hub: HubHarness
    first: _Tenant
    second: _Tenant

    def both(self) -> tuple[_Tenant, _Tenant]:
        return (self.first, self.second)


def _seeded(hub: HubHarness, *labels: str) -> list[_Tenant]:
    """Tenants named ``labels`` on ``hub``, seeded alike, each driven by one person who administers every one of
    them as a member and holds no hub-level role."""
    person = seed_user(hub, username="operator", role=Role.PENDING)
    session = seed_session(hub, person)
    tenants = []
    for label in labels:
        harness = hub.add_tenant(label)
        seed_membership(hub.engine, harness.tenant, person.user_id, Role.ADMIN)
        harness.client.headers["Cookie"] = f"bz_session={session}"
        tenants.append(_seed(hub, harness, label))
    return tenants


@pytest.fixture
def two(tmp_path: Path) -> _Two:
    hub = build_hub(tmp_path, auth_mode="oauth")
    first, second = _seeded(hub, "alpha", "beta")
    # Hub item refs count per tenant: the first tenant's probed item sits at a ref the second never allocated.
    item = _ok(first.client.post("/api/work-sources/hub/items", json={"title": "alpha-last", "body": "b"}), 201)
    first.ids["ref"] = str(item["ref"])
    first.markers.add("alpha-last")
    return _Two(hub, first, second)


# --- probing ------------------------------------------------------------------------------------------------------

_MINTED = re.compile(r"[a-z]+_[0-9A-Z]{26}")
_PARAM = re.compile(r"\{([^}:]+)(?::[^}]*)?\}")


def _ghost(value: str) -> str:
    """A value shaped like ``value`` that names no record in any tenant."""
    if value.isdigit():
        return str(int(value) + 9000)
    if _MINTED.fullmatch(value):
        return value[:-4] + "ZZZZ"
    return f"{value}-ghost"


#: Seeded names every tenant holds alike — the built-in ``hub`` source — which a never-minted record sits beside.
_BUILT_IN = frozenset({"hub_source"})


def _ghost_of(tenant: _Tenant) -> _Tenant:
    """``tenant`` with every id it seeded replaced by one never minted."""
    ghost = _Tenant(tenant.harness, f"{tenant.label}-ghost")
    ghost.ids = {key: value if key in _BUILT_IN else _ghost(value) for key, value in tenant.ids.items()}
    ghost.markers = set(ghost.ids.values())
    return ghost


def _url(path: str, tenant: _Tenant, ids: dict[str, str]) -> str:
    def value(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "name":
            return tenant.ids["repository" if path.startswith("/api/repositories") else "secret"]
        if name == "runner_id" and path.startswith("/api/fleet/"):
            return tenant.ids["runner"]
        return tenant.ids[ids.get(name, name)]

    return _PARAM.sub(value, path)


def _fill(body: object, tenant: _Tenant, ids: dict[str, str]) -> object:
    if body == "{seq}":
        return int(tenant.ids["seq"])
    if isinstance(body, str):
        return re.sub(r"\{(\w+)\}", lambda m: tenant.ids[ids.get(m.group(1), m.group(1))], body)
    if isinstance(body, list):
        return [_fill(item, tenant, ids) for item in body]
    if isinstance(body, dict):
        return {key: _fill(value, tenant, ids) for key, value in body.items()}
    return body


def _send(sender: _Tenant, owner: _Tenant, method: str, path: str, probe: _Probe) -> httpx.Response:
    """``method path`` from ``sender``, its path and body naming ``owner``'s records. A fleet call presents the
    sender's runner token and names the sender's runner; the configuration apply sends the sender's own export."""
    ids = probe.ids or {}
    url, body, params = _url(path, owner, ids), _fill(probe.body, owner, ids), _fill(probe.params, owner, ids)
    if path == "/api/config/apply":
        export = sender.client.get("/api/config/export")
        content_type = export.headers["content-type"]
        return sender.client.post(url, content=export.content, headers={"Content-Type": content_type})
    if not path.startswith("/api/fleet/"):
        return sender.client.request(method, url, json=body, params=params)
    if isinstance(body, dict):
        body = {"runner_id": sender.runner(), **body}
    bearer = {"Authorization": f"Bearer {runner_token(sender.runner())}"}
    params = {"runner_id": sender.runner(), **(params if isinstance(params, dict) else {})}
    return sender.client.request(method, url, json=body, params=params, headers=bearer)


def _reads(t: _Tenant) -> dict[str, tuple[int, str]]:
    """Every read the tenant's records answer: its listings, and each by-id read of its own records."""
    answers: dict[str, tuple[int, str]] = {}
    for (method, path), probe in _PROBES.items():
        if method != "GET" or probe.kind in {"stream", "global"}:
            continue
        response = _send(t, t, method, path, probe)
        answers[str(response.request.url)] = (response.status_code, response.text)
    return answers


def _unghosted(text: str, ghost: _Tenant, owner: _Tenant) -> str:
    for key, value in sorted(ghost.ids.items(), key=lambda item: -len(item[1])):
        text = text.replace(value, owner.ids[key])
    return text


def test_a_listing_in_either_tenant_names_none_of_the_others_records(two: _Two) -> None:
    for reader, other in ((two.first, two.second), (two.second, two.first)):
        for (method, path), probe in _PROBES.items():
            if probe.kind != "listing":
                continue
            response = _send(reader, reader, method, path, probe)
            assert response.status_code == 200, f"{path}: {response.text}"
            leaked = {marker for marker in other.markers if marker in response.text}
            assert not leaked, f"{reader.label} reads {other.label}'s {sorted(leaked)} at {path}"


def test_a_tenant_sees_its_own_records_in_its_listings(two: _Two) -> None:
    for t in two.both():
        for path, marker in (
            ("/api/chunks", t.ids["chunk_id"]),
            ("/api/graphs", t.ids["graph_id"]),
            ("/api/scopes", t.ids["slug"]),
            ("/api/routines", t.ids["routine_id"]),
            ("/api/secrets", t.ids["secret"]),
            ("/api/repositories", t.ids["repository"]),
            ("/api/work-sources", t.ids["source"]),
            ("/api/findings", t.ids["finding_id"]),
            ("/api/garden-proposals", t.ids["proposal_id"]),
            ("/api/invitations", t.ids["invitation_id"]),
            ("/api/runners", t.ids["runner_id"]),
            ("/api/members", t.ids["user_id"]),
            ("/api/questions", t.ids["question_id"]),
        ):
            assert marker in t.client.get(path).text, f"{t.label} misses its own {marker} at {path}"


def test_naming_the_other_tenants_record_answers_as_a_never_minted_one_and_changes_nothing_there(two: _Two) -> None:
    owner, sender, ghost = two.first, two.second, _ghost_of(two.first)
    before = _reads(owner)
    wrong: dict[str, str] = {}
    for (method, path), probe in _PROBES.items():
        if probe.kind != "foreign":
            continue
        foreign = _send(sender, owner, method, path, probe)
        unknown = _send(sender, ghost, method, path, probe)
        if foreign.status_code != probe.status:
            wrong[f"{method} {path}"] = f"{foreign.status_code} {foreign.text[:200]}"
        elif (foreign.status_code, foreign.text) != (unknown.status_code, _unghosted(unknown.text, ghost, owner)):
            wrong[f"{method} {path}"] = f"{foreign.text[:160]} -- never minted: {unknown.text[:160]}"
    assert not wrong, "\n".join(f"{route}: {answer}" for route, answer in wrong.items())
    assert _reads(owner) == before


def _blind(owner: _Tenant, method: str, path: str, probe: _Probe) -> str:
    """What ``owner`` answers naming its own records, when that is what it answers naming records never minted;
    ``""`` when the two differ."""
    ghost = _ghost_of(owner)
    unknown = _send(owner, ghost, method, path, probe)
    own = _send(owner, owner, method, path, probe)
    same = (own.status_code, own.text) == (unknown.status_code, _unghosted(unknown.text, ghost, owner))
    return f"{own.status_code} {own.text[:160]}" if same else ""


def _fresh_tenant(tmp_path: Path) -> _Tenant:
    tmp_path.mkdir()
    (owner,) = _seeded(build_hub(tmp_path, auth_mode="oauth"), "alpha")
    return owner


def test_each_foreign_probe_answers_its_records_own_tenant_otherwise_than_a_never_minted_record(
    tmp_path: Path,
) -> None:
    """A foreign probe can see a leak only if its record, named by its own tenant, answers otherwise than a record
    never minted. The probes take turns on one seeded tenant — reads first, removals last — and one whose record an
    earlier probe used up is asked again of a tenant seeded afresh."""
    shared = _fresh_tenant(tmp_path / "shared")
    foreign = [(route, probe) for route, probe in _PROBES.items() if probe.kind == "foreign"]
    foreign.sort(key=lambda entry: (entry[0][0] != "GET", entry[0][0] == "DELETE" or entry[0][1].endswith("/retire")))
    blind: dict[str, str] = {}
    for turn, ((method, path), probe) in enumerate(foreign):
        if _blind(shared, method, path, probe) and (
            answer := _blind(_fresh_tenant(tmp_path / f"turn-{turn}"), method, path, probe)
        ):
            blind[f"{method} {path}"] = answer
    assert not blind, "\n".join(f"{route}: {answer}" for route, answer in blind.items())


def test_a_write_in_one_tenant_changes_nothing_the_other_reads(two: _Two) -> None:
    before = _reads(two.first)
    wrong: dict[str, str] = {}
    for (method, path), probe in _PROBES.items():
        if probe.kind != "own":
            continue
        response = _send(two.second, two.second, method, path, probe)
        if response.status_code != probe.status:
            wrong[f"{method} {path}"] = f"{response.status_code} {response.text[:200]}"
    assert not wrong, "\n".join(f"{route}: {answer}" for route, answer in wrong.items())
    assert _reads(two.first) == before


def _project_answers(stores: TenantStores, project_id: ProjectId, owner: _Tenant, at: datetime) -> tuple[object, ...]:
    """What ``stores`` answers for ``project_id`` and the source and repository ``owner`` linked it to."""
    links = stores.project_links
    source, repository = owner.ids["source"], owner.ids["repository"]
    return (
        stores.projects.get(project_id),
        stores.projects.get_many([project_id]),
        links.source_link(project_id, source),
        links.repository_link(project_id, repository),
        links.sources_of(project_id),
        links.repositories_of(project_id),
        links.linked_at(project_id, source, at),
    )


def test_a_tenants_projects_and_links_answer_only_in_their_own_tenant(two: _Two) -> None:
    """At the store, as no route names a project: the other tenant's project, slugs and links answer as a project never
    minted does, while their own tenant reads them."""
    core = build_hub_core(two.hub.engine, clock=two.hub.clock)
    stores = {t.label: core.open(StoreScope(t.harness.tenant)) for t in two.both()}
    projects = {}
    for t in two.both():
        own = stores[t.label]
        authoring = ProjectAuthoring(projects=own.projects, links=own.project_links, clock=two.hub.clock)
        [project] = own.projects.list_all()
        source, repository = (
            own.work_source_records.get(t.ids["source"]),
            own.repository_records.get(t.ids["repository"]),
        )
        assert source is not None and repository is not None
        authoring.link_source(project, source, OP)
        authoring.link_repository(project, repository, OP)
        projects[t.label] = authoring.edit(project, ProjectEdit(slug=f"{t.label}-renamed"), OP)
    never = ProjectId(Id.mint_at(IdPrefix.PROJECT, two.hub.clock.now()).value)
    at = two.hub.clock.now()
    for owner, sender in ((two.first, two.second), (two.second, two.first)):
        project = projects[owner.label]
        own, foreign = stores[owner.label], stores[sender.label]
        unknown = _project_answers(own, never, owner, at)
        assert _project_answers(own, project.project_id, owner, at) != unknown
        assert _project_answers(foreign, project.project_id, owner, at) == unknown
        slug = f"{owner.label}-renamed"
        assert own.projects.resolve(slug) == project
        assert foreign.projects.resolve(slug) is None
        assert project.project_id in own.project_links.projects_linking_source(owner.ids["source"])
        assert project.project_id not in foreign.project_links.projects_linking_source(owner.ids["source"])


def test_each_tenants_stream_replays_its_own_events_only(two: _Two) -> None:
    for reader, other in ((two.first, two.second), (two.second, two.first)):
        frames = asyncio.run(drain_stream(reader.harness.services.events))
        text = repr(frames)
        assert reader.ids["chunk_id"] in text
        assert not {marker for marker in other.markers if marker in text}


def test_a_runner_minted_id_posted_in_both_tenants_is_two_rows_each_answered_alone(two: _Two) -> None:
    answers = {}
    for t in (two.first, two.second):
        answered = t.client.post(f"/api/questions/{_SHARED_QUESTION}/answers", json={"answer": f"{t.label}-answer"})
        assert answered.status_code < 300, answered.text
    for t in (two.first, two.second):
        polled = t.client.get(f"/api/fleet/questions/{_SHARED_QUESTION}", params={"runner_id": t.runner()})
        assert polled.status_code == 200, polled.text
        answers[t.label] = polled.text
        segment = t.client.get(f"/api/chunks/{t.ids['chunk_id']}/transcripts/{_SHARED_SEGMENT}")
        assert f"{t.label}-transcript" in segment.text
    assert "alpha-answer" in answers["alpha"] and "beta-answer" not in answers["alpha"]
    assert "beta-answer" in answers["beta"] and "alpha-answer" not in answers["beta"]


def test_a_route_token_acts_in_its_chunks_tenant_and_nowhere_else(two: _Two) -> None:
    first, second = two.first, two.second
    lease = {"chunk_id": first.ids["chunk_id"], "epoch": 2, "route_token": first.ids["route_token"]}
    fact = [{"seq": 50, "kind": "lease.minted", "payload": lease}]

    borrowed = second.client.post("/api/fleet/events", json={"runner_id": second.runner(), "facts": fact})
    rekey = second.client.post(
        f"/api/fleet/chunks/{first.ids['chunk_id']}/route-token", json={"runner_id": second.runner()}
    )
    named_elsewhere = first.client.post(
        "/api/fleet/events",
        json={"runner_id": first.runner(), "facts": fact},
        headers={"X-Blizzard-Tenant": second.harness.tenant},
    )
    own = first.client.post("/api/fleet/events", json={"runner_id": first.runner(), "facts": fact})

    assert borrowed.status_code == 200 and borrowed.json()["rejected"], borrowed.text
    assert rekey.status_code == 404
    assert named_elsewhere.status_code == 400
    assert own.status_code == 200 and not own.json()["rejected"], own.text


def test_the_packaged_system_artifacts_read_the_same_from_either_tenant(two: _Two) -> None:
    listings = [t.client.get("/api/fleet/system-artifacts", params={"runner_id": t.runner()}) for t in two.both()]
    assert listings[0].status_code == 200
    assert listings[0].text == listings[1].text
    names = [entry["name"] for entry in listings[0].json()]
    assert names
    for name in names:
        bodies = [
            t.client.get(f"/api/fleet/system-artifacts/{name}", params={"runner_id": t.runner()}).content
            for t in two.both()
        ]
        assert bodies[0] == bodies[1]


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/egress/status", None),
        ("POST", "/api/egress/reset", {"dataset": "events", "to": "2026-01-01T00:00:00Z"}),
        ("POST", "/api/egress/backfill", {"since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"}),
        ("GET", "/api/traces/status", None),
        ("POST", "/api/traces/replay", {"since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"}),
    ],
)
def test_the_export_routes_refuse_a_tenant_administrator(tmp_path: Path, method: str, path: str, body: object) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="tenant-admin", role=Role.ADMIN)
    cookie = {"Cookie": f"bz_session={seed_session(hub, admin)}"}

    assert hub.client.request(method, path, json=body, headers=cookie).status_code == 403


# --- a booted hub holding two open tenants and a closed one runs every sweep -------------------------------------


def _rows(hub: HubHarness, table: sa.Table, tenant: str) -> int:
    with hub.engine.connect() as conn:
        count = sa.select(sa.func.count()).select_from(table).where(table.c.tenant_id == tenant)
        return conn.execute(hub_scoped(count)).scalar_one()


def test_every_sweep_acts_in_each_open_tenant_and_opens_no_closed_one(tmp_path: Path) -> None:
    hub = build_hub(
        tmp_path,
        auth_mode="oauth",
        trace_exporter=(spans := InMemoryTraceExporter()),
        tracing=TracingConfig(settle_seconds=0),
        egress=EgressConfig(directory=tmp_path / "egress", settle_seconds=0),
        egress_path_key=b"k" * 32,
    )
    first, second, closed = _seeded(hub, "alpha", "beta", "gamma")
    intents = {t.label: _landed_hub_ref(hub, t.harness.tenant, f"ch_close_{t.label}") for t in (first, second, closed)}
    close_tenant(hub.engine, closed.harness.tenant)
    hub.hub_services.tenants.open_tenants.refresh()
    assert hub.app is not None
    sweeps = {sweep.name: sweep.reconciler for sweep in Sweep.all(hub.app)}
    passes = ("forge_status", "transcript_events", "work_closure", "trace_export", "egress")
    assert set(sweeps) == {*passes, "tenant_teardown"}

    hub.clock.advance(timedelta(hours=1))
    with capture_statements(hub.engine) as statements:
        for name in passes:
            sweeps[name].sweep()

    for t in (first, second):
        assert _rows(hub, schema.transcript_event_derivations, t.harness.tenant) == 1, t.label
        assert intents[t.label].delivery.pending_close_intents() == []
        assert _close_events(hub, t.harness.tenant) == [f"ch_close_{t.label}"]
    assert [i.chunk_id for i in intents["gamma"].delivery.pending_close_intents()] == ["ch_close_gamma"]
    assert _rows(hub, schema.transcript_event_derivations, closed.harness.tenant) == 0
    assert closed.harness.tenant not in repr(spans.batches)
    written = b"".join(path.read_bytes() for path in (tmp_path / "egress").rglob("*") if path.is_file())
    assert closed.harness.tenant.encode() not in written
    assert not [sql for sql, params in statements if closed.harness.tenant in repr(params)]

    sweeps["tenant_teardown"].sweep()

    tenant_owned = [table for table in schema.metadata.sorted_tables if table.name not in schema.GLOBAL_TABLES]
    held = {table.name: _rows(hub, table, closed.harness.tenant) for table in tenant_owned}
    assert not {name: count for name, count in held.items() if count}
    for t in (first, second):
        assert t.client.get(f"/api/chunks/{t.ids['chunk_id']}").status_code == 200
