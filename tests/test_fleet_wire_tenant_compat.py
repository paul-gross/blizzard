"""The fleet wire under tenancy only gains fields (component tier): against the committed hub spec,
no ``/api/fleet`` path, request, parameter or enum changes, the only surface responses that grow
by a tenant are registration's and the identity route's, every other model a fleet path reaches
grows by optional project fields alone, and a runner's own hub client registers and is identified
in a tenant other than the first, reading that tenant from both answers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from blizzard.hub.app import create_app_for_export
from blizzard.runner.hub.client import TokenIdentity
from blizzard.runner.hub.internal.http_hub import HttpHubClient
from blizzard.tools.wire_compat import classify_spec_diff
from tests.support import add_tenant, build_hub, seed_runner

pytestmark = pytest.mark.component

_COMMITTED_SPEC = Path(__file__).resolve().parents[1] / "openapi" / "hub.openapi.json"
_FLEET = "/api/fleet"
_METHODS = ("get", "post", "put", "patch", "delete")
#: The two responses that name the runner's tenant, and the fields each gains.
_GROWN = {"RunnerRegistrationResponse", "RunnerIdentityView"}
_TENANT_FIELDS = {"tenant_id", "tenant_name"}
#: The optional project fields a fleet-reached model may gain: a chunk's or garden record's project, a runner's
#: served projects, and why a claim was refused.
_PROJECT_FIELDS = {"project_id", "project_slug", "project_name", "projects", "reason"}


def _committed() -> dict[str, Any]:
    return json.loads(_COMMITTED_SPEC.read_text())


def _live() -> dict[str, Any]:
    return create_app_for_export().openapi()


def _fleet_operations(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (path, method): op
        for path, item in spec["paths"].items()
        if path.startswith(_FLEET)
        for method, op in item.items()
        if method in _METHODS
    }


def _refs(node: object) -> set[str]:
    if isinstance(node, dict):
        found = {node["$ref"].rsplit("/", 1)[-1]} if isinstance(node.get("$ref"), str) else set()
        return found.union(*(_refs(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(_refs(value) for value in node))
    return set()


def _reachable(spec: dict[str, Any]) -> set[str]:
    """Every component schema an ``/api/fleet`` operation reaches, transitively."""
    schemas = spec["components"]["schemas"]
    pending = _refs(list(_fleet_operations(spec).values()))
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen or name not in schemas:
            continue
        seen.add(name)
        pending |= _refs(schemas[name])
    return seen


def test_no_fleet_path_request_parameter_or_enum_changes_and_responses_grow_only_by_optional_fields() -> None:
    committed, live = _committed(), _live()
    before, after = _fleet_operations(committed), _fleet_operations(live)

    assert classify_spec_diff(committed, live) == []
    assert sorted(after) == sorted(before)
    for key, op in before.items():
        assert after[key].get("requestBody") == op.get("requestBody"), key
        assert after[key].get("parameters") == op.get("parameters"), key
    base, head = committed["components"]["schemas"], live["components"]["schemas"]
    for name in sorted(_reachable(committed)):
        was, now = base[name], head[name]
        assert now.get("enum") == was.get("enum"), name
        assert now.get("required") == was.get("required"), name
        gained = set(now.get("properties", {})) - set(was.get("properties", {}))
        assert set(was.get("properties", {})) <= set(now.get("properties", {})), name
        assert gained <= (_TENANT_FIELDS if name in _GROWN else set()) | _PROJECT_FIELDS, name
    assert set(head["RunnerRegistrationResponse"]["properties"]) >= _TENANT_FIELDS
    assert set(head["RunnerIdentityView"]["properties"]) >= _TENANT_FIELDS


def test_a_runners_own_hub_client_registers_and_is_identified_in_another_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    bravo = add_tenant(hub.engine, "bravo")
    token = seed_runner(hub.open_services(bravo), "rn-bravo-1", name="r-bravo", register=False)
    assert hub.app is not None
    client = TestClient(hub.app, headers={"Authorization": f"Bearer {token}"})
    runner = HttpHubClient(client)

    reply = runner.register_runner("r-bravo", "w1")

    assert (reply.runner_id, reply.runner_name, reply.first_registration) == ("rn-bravo-1", "r-bravo", True)
    assert (reply.tenant_id, reply.tenant_name) == (bravo, "bravo")
    assert runner.identity() == TokenIdentity(
        runner_id="rn-bravo-1", runner_name="r-bravo", tenant_id=bravo, tenant_name="bravo"
    )
    assert runner.fetch_runner_paused("rn-bravo-1") is False
    assert runner.peek_queue((), policy="pass-over") == []
