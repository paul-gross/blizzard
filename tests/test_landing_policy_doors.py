"""A repository's landing policy and a project's landing order through the hub's doors: the repository routes take and
answer ``landing_policy`` and ``park_timeout_hours`` — an unknown policy refused, ``null`` clearing the timeout alone —
the schema lists each policy with what it requires and the forge detail what it offers; a project's ``landing-order``
writes rank its links, a repeated one writing no fact, and refuse a repository it does not link; and the
configuration document carries both, an exported document applying back without a write."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from blizzard.hub.domain.config.landing_policies import POLICY_CAPABILITIES, LandingPolicy
from blizzard.hub.store.schema import project_repository_positions
from blizzard.wire.repository import LandingPolicyName
from tests.support import HubHarness, build_hub, default_tenant

pytestmark = pytest.mark.component


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path, link=False)
    _ok(hub.client.post("/api/secrets", json={"name": "token", "value": "v"}), 201)
    for slug in ("acme-app", "acme-lib"):
        _ok(hub.client.post("/api/repositories", json=_repository(slug)), 201)
    _ok(hub.client.post("/api/projects", json={"slug": "widgets", "name": "Widgets"}), 201)
    return hub


def _repository(slug: str, **extra: object) -> dict[str, object]:
    return {
        "slug": slug,
        "forge_api_url": "https://forge.example",
        "owner": "acme",
        "repo": slug,
        "base_branch": "main",
        "secret_name": "token",
        **extra,
    }


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def _refusal(response: Any) -> tuple[str, str]:
    """The field and code of a 422's one refusal."""
    assert response.status_code == 422, response.text
    (detail,) = response.json()["detail"]
    return detail["loc"][-1], detail["type"]


def _link_both(client: TestClient) -> None:
    for slug in ("acme-app", "acme-lib"):
        _ok(client.put(f"/api/projects/widgets/repository-links/{slug}"))


def _order(client: TestClient) -> list[tuple[str, float | None]]:
    links = _ok(client.get("/api/projects/widgets/repository-links"))["repository_links"]
    return [(link["slug"], link["position"]) for link in links]


def _facts(hub: HubHarness) -> int:
    with hub.engine.connect() as conn:
        facts = project_repository_positions
        tenant = default_tenant(hub.engine)
        return conn.execute(select(func.count()).select_from(facts).where(facts.c.tenant_id == tenant)).scalar_one()


# --- The repository's fields -------------------------------------------------------------------------------------


def test_a_repository_created_without_a_policy_lands_by_merge_on_checks(hub: HubHarness) -> None:
    shown = _ok(hub.client.get("/api/repositories/acme-app"))

    assert (shown["landing_policy"], shown["park_timeout_hours"]) == ("merge-on-checks", None)


def test_an_unknown_policy_is_refused_on_create_and_on_a_patch(hub: HubHarness) -> None:
    created = hub.client.post("/api/repositories", json=_repository("acme-unknown", landing_policy="unknown"))
    patched = hub.client.patch("/api/repositories/acme-app", json={"landing_policy": "unknown"})

    assert _refusal(created)[0] == "landing_policy"
    assert _refusal(patched)[0] == "landing_policy"
    assert hub.services.repository_records.resolve("acme-park") is None


def test_a_null_patch_clears_the_timeout_and_null_on_any_other_field_is_refused(hub: HubHarness) -> None:
    before = _ok(hub.client.get("/api/repositories/acme-app"))

    cleared = _ok(hub.client.patch("/api/repositories/acme-app", json={"park_timeout_hours": None}))
    owner = hub.client.patch("/api/repositories/acme-app", json={"owner": None})
    policy = hub.client.patch("/api/repositories/acme-app", json={"landing_policy": None})

    assert (cleared["park_timeout_hours"], cleared["revision"]) == (None, before["revision"])
    assert _refusal(owner)[0] == "owner"
    assert _refusal(policy)[0] == "landing_policy"


def test_a_timeout_on_a_repository_that_does_not_park_is_refused(hub: HubHarness) -> None:
    refused = hub.client.patch("/api/repositories/acme-app", json={"park_timeout_hours": 5})

    assert _refusal(refused)[0] == "park_timeout_hours"


def test_the_repository_schema_lists_every_policy_with_what_it_requires(hub: HubHarness) -> None:
    schema = _ok(hub.client.get("/api/config/schema/repositories"))

    listed = {entry["policy"]: entry for entry in schema["x-blizzard-landing-policies"]}
    assert set(listed) == {policy.value for policy in LandingPolicy}
    needs = POLICY_CAPABILITIES[LandingPolicy.MERGE_ON_CHECKS]
    assert listed["merge-on-checks"]["requires"] == sorted(needs.requires)
    assert listed["merge-on-checks"]["uses"] == sorted(needs.uses)


def test_the_forge_detail_carries_what_it_offers(hub: HubHarness) -> None:
    forge = _ok(hub.client.get("/api/repositories/acme-app"))["forge_id"]

    offered = _ok(hub.client.get(f"/api/forges/{forge}"))["offered"]

    assert offered == sorted(offered)
    assert POLICY_CAPABILITIES[LandingPolicy.MERGE_ON_CHECKS].requires <= set(offered)


def test_the_wire_names_every_policy_the_domain_knows() -> None:
    assert {policy.value for policy in LandingPolicyName} == {policy.value for policy in LandingPolicy}


# --- The project's landing order ---------------------------------------------------------------------------------


def test_links_list_unranked_by_slug_until_a_whole_order_ranks_them(hub: HubHarness) -> None:
    _link_both(hub.client)
    unranked = _order(hub.client)

    answered = _ok(
        hub.client.put("/api/projects/widgets/landing-order", json={"repositories": ["acme-lib", "acme-app"]})
    )

    assert unranked == [("acme-app", None), ("acme-lib", None)]
    assert [(link["slug"], link["position"]) for link in answered["repository_links"]] == [
        ("acme-lib", 1.0),
        ("acme-app", 2.0),
    ]
    assert _order(hub.client) == [("acme-lib", 1.0), ("acme-app", 2.0)]


def test_putting_the_order_the_project_has_writes_no_fact(hub: HubHarness) -> None:
    _link_both(hub.client)
    order = {"repositories": ["acme-lib", "acme-app"]}
    _ok(hub.client.put("/api/projects/widgets/landing-order", json=order))
    written = _facts(hub)

    _ok(hub.client.put("/api/projects/widgets/landing-order", json=order))

    assert (written, _facts(hub)) == (2, 2)


def test_a_position_moves_one_repository_first_after_another_or_to_unranked(hub: HubHarness) -> None:
    _link_both(hub.client)
    _ok(hub.client.put("/api/projects/widgets/landing-order", json={"repositories": ["acme-lib", "acme-app"]}))
    move = "/api/projects/widgets/landing-order/position"

    _ok(hub.client.post(move, json={"repository": "acme-app"}))
    first = _order(hub.client)
    _ok(hub.client.post(move, json={"repository": "acme-app", "after_repository": "acme-lib"}))
    after = _order(hub.client)
    _ok(hub.client.post(move, json={"repository": "acme-lib", "unranked": True}))

    assert first == [("acme-app", 0.0), ("acme-lib", 1.0)]
    assert after == [("acme-lib", 1.0), ("acme-app", 2.0)]
    assert _order(hub.client) == [("acme-app", 2.0), ("acme-lib", None)]


@pytest.mark.parametrize(
    ("path", "body", "field"),
    [
        ("/api/projects/widgets/landing-order", {"repositories": ["acme-lib"]}, "repositories"),
        ("/api/projects/widgets/landing-order", {"repositories": ["nowhere"]}, "repositories"),
        ("/api/projects/widgets/landing-order/position", {"repository": "acme-lib"}, "repository"),
        (
            "/api/projects/widgets/landing-order/position",
            {"repository": "acme-app", "after_repository": "acme-lib"},
            "after_repository",
        ),
    ],
)
def test_a_repository_the_project_does_not_link_is_refused_repository_not_linked(
    hub: HubHarness, path: str, body: dict[str, object], field: str
) -> None:
    _ok(hub.client.put("/api/projects/widgets/repository-links/acme-app"))

    refused = hub.client.post(path, json=body) if path.endswith("position") else hub.client.put(path, json=body)

    assert _refusal(refused) == (field, "repository_not_linked")
    assert _facts(hub) == 0


# --- The configuration document ----------------------------------------------------------------------------------


def _apply(client: TestClient, document: dict[str, object]) -> Any:
    return client.post("/api/config/apply", json=document)


def _written(applied: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(o["kind"], o["key"], o["op"]) for o in applied["outcomes"] if o["op"] != "unchanged"]


@pytest.mark.parametrize("version", [1, 2])
def test_an_unknown_policy_in_a_document_is_refused(hub: HubHarness, version: int) -> None:
    document = _ok(hub.client.get("/api/config/export", params={"version": version}))
    document["repositories"][0]["landing_policy"] = "unknown"

    refused = _apply(hub.client, document)

    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"][0]["loc"][-1] == "landing_policy"


@pytest.mark.parametrize("version", [1, 2])
def test_an_exported_landing_order_applies_back_writing_nothing(hub: HubHarness, version: int) -> None:
    _link_both(hub.client)
    _ok(hub.client.put("/api/projects/widgets/landing-order", json={"repositories": ["acme-lib"]}))

    document = _ok(hub.client.get("/api/config/export", params={"version": version}))
    facts = _facts(hub)
    applied = _ok(_apply(hub.client, document))

    widgets = next(project for project in document["projects"] if project["slug"] == "widgets")
    default = next(project for project in document["projects"] if project["slug"] == "default")
    assert widgets["repositories"] == ["acme-app", "acme-lib"]
    assert widgets["landing_order"] == ["acme-lib"]
    assert "landing_order" not in default
    assert {(r["landing_policy"], r["park_timeout_hours"]) for r in document["repositories"]} == {
        ("merge-on-checks", None)
    }
    assert _written(applied) == []
    assert _facts(hub) == facts


def test_a_document_links_and_ranks_a_repository_together_and_reports_the_order(hub: HubHarness) -> None:
    document = {
        "version": 1,
        "projects": [
            {"slug": "widgets", "repositories": ["acme-app", "acme-lib"], "landing_order": ["acme-lib", "acme-app"]}
        ],
    }

    applied = _ok(_apply(hub.client, document))
    again = _ok(_apply(hub.client, document))

    assert _order(hub.client) == [("acme-lib", 1.0), ("acme-app", 2.0)]
    order_rows = [o for o in applied["outcomes"] if o["diff"] and o["diff"][0]["field"] == "landing_order"]
    assert [(row["kind"], row["key"], row["diff"][0]["new"]) for row in order_rows] == [
        ("project", "widgets", ["acme-lib", "acme-app"])
    ]
    assert _written(again) == []
    assert _facts(hub) == 2


def test_a_document_that_omits_the_landing_order_leaves_it(hub: HubHarness) -> None:
    _link_both(hub.client)
    _ok(hub.client.put("/api/projects/widgets/landing-order", json={"repositories": ["acme-lib"]}))

    _ok(_apply(hub.client, {"version": 1, "projects": [{"slug": "widgets", "name": "Widgets"}]}))

    assert _order(hub.client) == [("acme-lib", 1.0), ("acme-app", None)]


def test_a_landing_order_naming_a_repository_the_entry_does_not_link_is_refused(hub: HubHarness) -> None:
    document = {
        "version": 1,
        "projects": [{"slug": "widgets", "repositories": ["acme-app"], "landing_order": ["acme-lib"]}],
    }

    refused = _apply(hub.client, document)

    assert _refusal(refused) == ("landing_order", "repository_not_linked")
    assert _facts(hub) == 0
    assert _ok(hub.client.get("/api/projects/widgets/repository-links"))["repository_links"] == []


def test_a_dry_run_reports_the_order_and_writes_no_fact(hub: HubHarness) -> None:
    _link_both(hub.client)
    document = {"version": 1, "projects": [{"slug": "widgets", "landing_order": ["acme-app"]}]}

    previewed = _ok(hub.client.post("/api/config/apply", params={"dry_run": "true"}, json=document))

    assert _written(previewed) == [("project", "widgets", "edit")]
    assert _facts(hub) == 0
