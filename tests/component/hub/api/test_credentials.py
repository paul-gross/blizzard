"""Credential record routes, and repositories and work sources naming a credential, over a tenant-scoped hub
(component tier): the six verbs, each refusal of both refusal tables with its status and code, every row of the
old-field table with what it answers, a body written before ``credential`` existed, and the capability gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest
import sqlalchemy as sa

from blizzard.auth_core import Role
from blizzard.hub.store import schema as s
from blizzard.wire.credential import CredentialDocument, CredentialSummary
from blizzard.wire.repository import RepositorySummary
from blizzard.wire.work_source import WorkSourceSummary
from tests.support.scaffolding import (
    FIXTURE_FORGE_SECRET,
    HubHarness,
    build_hub,
    ensure_fixture_secret,
    hub_scoped,
    seed_session,
    seed_user,
)

_SPARE = "spare-token"


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    harness = build_hub(tmp_path, repositories=(), link=False)
    ensure_fixture_secret(harness.client)
    for name in (_SPARE, "third-token"):
        assert harness.client.post("/api/secrets", json={"name": name, "value": "v"}).status_code == 201
    for slug, api_base in (("ghe", "https://ghe.example/api/v3"), ("other", "https://other.example")):
        forge = harness.client.post("/api/forges", json={"slug": slug, "kind": "github", "api_base": api_base})
        assert forge.status_code == 201, forge.text
    return harness


def _credential(
    hub: HubHarness, slug: str = "deploy", *, forge: str = "ghe", secret: str = _SPARE, **settings: object
) -> dict[str, Any]:
    response = hub.client.post(
        "/api/credentials",
        json={"slug": slug, "forge": forge, "binding": "github.token", "secret": secret, "settings": settings},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _repository(**fields: object) -> dict[str, object]:
    return {"slug": "demo", "forge": "ghe", "owner": "acme", "repo": "demo", "base_branch": "main", **fields}


def _source(**fields: object) -> dict[str, object]:
    return {"slug": "issues", "forge": "ghe", "provider": "github", "locator": "acme/issues", **fields}


def _code(response: httpx2.Response) -> tuple[int, str]:
    """The status and refusal a problem answers: a field refusal's code, else the problem's type."""
    problem = response.json()
    if isinstance(problem["detail"], list):
        return response.status_code, cast(str, problem["detail"][0]["type"])
    return response.status_code, cast(str, problem["type"]).removeprefix("urn:blizzard:problem:")


def _old_field_events(hub: HubHarness) -> list[dict[str, Any]]:
    with hub.engine.connect() as conn:
        rows = conn.execute(
            hub_scoped(
                sa.select(s.event_log.c.severity, s.event_log.c.detail)
                .where(s.event_log.c.kind == "config-old-field-used")
                .order_by(s.event_log.c.id)
            )
        ).all()
    return [{"severity": severity, **json.loads(detail)} for severity, detail in rows]


def test_a_credential_is_created_listed_read_renamed_retired_and_enabled(hub: HubHarness) -> None:
    created = _credential(hub, variant="classic")
    assert CredentialSummary.model_validate(created).authority == f"secret/{_SPARE}"
    assert (created["revision"], created["forge_slug"], created["settings"]) == (1, "ghe", {"variant": "classic"})
    assert hub.client.get("/api/credentials").json()["credentials"] == [created]
    document = hub.client.get(f"/api/credentials/{created['credential_id']}").json()
    assert CredentialDocument.model_validate(document).referrers == []

    renamed = hub.client.patch("/api/credentials/deploy", json={"slug": "deploy-2", "name": "Deploy"})
    assert (renamed.status_code, renamed.json()["revision"]) == (200, 2), renamed.text
    assert hub.client.get("/api/credentials/deploy").json()["slug"] == "deploy-2"
    assert hub.client.patch("/api/credentials/deploy-2", json={}).json()["revision"] == 2
    stale = hub.client.patch("/api/credentials/deploy-2", json={"name": "x"}, headers={"If-Match": "1"})
    assert _code(stale) == (409, "revision-conflict")
    assert "revision 2" in stale.json()["detail"]

    assert hub.client.post("/api/credentials/deploy-2/retire").json()["retired"] is True
    assert hub.client.get("/api/credentials").json()["credentials"] == []
    assert len(hub.client.get("/api/credentials", params={"include_retired": True}).json()["credentials"]) == 1
    assert hub.client.post("/api/credentials/deploy-2/enable").json()["retired"] is False
    assert hub.client.get("/api/credentials/unknown").status_code == 404


def test_each_credential_refusal_answers_its_status_and_code(hub: HubHarness) -> None:
    def create(**overrides: object) -> httpx2.Response:
        body = {"slug": "deploy", "forge": "ghe", "binding": "github.token", "secret": _SPARE, **overrides}
        return hub.client.post("/api/credentials", json=body)

    assert _code(create(binding="github.oauth")) == (422, "credential_binding_unknown")
    assert _code(create(settings={"variant": "enterprise"})) == (422, "credential_settings_invalid")
    assert _code(create(forge="nowhere")) == (422, "forge_unavailable")
    assert _code(create(secret="no-such-secret")) == (422, "secret_unavailable")
    held = _credential(hub)
    taken = create(slug="again")
    assert _code(taken) == (409, "credential-authority-taken")
    assert (taken.json()["holder"], taken.json()["authority"]) == ("deploy", f"secret/{_SPARE}")
    assert _code(create(secret="third-token")) == (409, "credential-slug-taken")
    for field, value in (("forge", "other"), ("binding", "github.token")):
        assert _code(hub.client.patch("/api/credentials/deploy", json={field: value})) == (
            422,
            "credential_binding_immutable",
        )

    repository = hub.client.post("/api/repositories", json=_repository(credential="deploy")).json()
    in_use = hub.client.post("/api/credentials/deploy/retire")
    assert _code(in_use) == (409, "credential-in-use")
    assert in_use.json()["referrers"] == [{"kind": "repository", "key": repository["repository_id"], "label": "demo"}]
    narrowed = hub.client.patch("/api/credentials/deploy", json={"settings": {"variant": "fine-grained"}})
    assert _code(narrowed) == (422, "credential-capability-missing")
    assert (narrowed.json()["referrers"], narrowed.json()["missing"]) == (in_use.json()["referrers"], ["checks.read"])
    assert hub.client.get("/api/credentials/deploy").json()["revision"] == held["revision"]

    with hub.engine.begin() as conn:
        conn.execute(hub_scoped(sa.update(s.forges).where(s.forges.c.slug == "other").values(kind="gitlab")))
    assert _code(create(slug="lab", forge="other", secret="third-token")) == (422, "credential_forge_kind_mismatch")


def test_each_referrer_refusal_answers_its_status_and_code(hub: HubHarness) -> None:
    _credential(hub)
    _credential(hub, "elsewhere", forge="other", secret="third-token")
    _credential(hub, "fine", secret=FIXTURE_FORGE_SECRET, variant="fine-grained")
    post = hub.client.post

    assert _code(post("/api/repositories", json=_repository(credential="nothing"))) == (422, "credential_unavailable")
    assert _code(post("/api/repositories", json=_repository(credential="elsewhere"))) == (
        422,
        "credential_forge_mismatch",
    )
    assert _code(post("/api/repositories", json=_repository(credential="deploy", secret_name="third-token"))) == (
        422,
        "credential_conflict",
    )
    refused = post("/api/repositories", json=_repository(credential="fine"))
    assert _code(refused) == (422, "forge_capability_missing")
    assert "through credential fine" in refused.json()["detail"][0]["msg"]
    neither = post("/api/repositories", json=_repository())
    assert (_code(neither), neither.json()["detail"][0]["loc"]) == ((422, "value_error"), ["body", "credential"])
    assert _code(post("/api/work-sources", json=_source(credential="elsewhere"))) == (
        422,
        "credential_forge_mismatch",
    )
    hub.client.post("/api/credentials/elsewhere/retire")
    assert _code(post("/api/work-sources", json=_source(forge="other", credential="elsewhere"))) == (
        422,
        "credential_unavailable",
    )
    assert hub.client.get("/api/repositories").json()["repositories"] == []

    parked = post("/api/repositories", json=_repository(credential="fine", landing_policy="park"))
    assert parked.status_code == 201, parked.text
    moved = hub.client.patch("/api/repositories/demo", json={"forge": "other"})
    assert _code(moved) == (422, "credential_forge_mismatch")
    repointed = hub.client.patch("/api/repositories/demo", json={"secret_name": _SPARE})
    assert (repointed.status_code, repointed.json()["credential_slug"]) == (200, "deploy"), repointed.text


def test_the_old_field_table_answers_a_deprecation_only_when_an_old_field_resolves(hub: HubHarness) -> None:
    deploy = _credential(hub)

    named = hub.client.post("/api/repositories", json=_repository(credential="deploy"))
    agreeing = hub.client.post(
        "/api/repositories", json=_repository(slug="agreeing", repo="agreeing", credential="deploy", secret_name=_SPARE)
    )
    unchanged = hub.client.patch("/api/repositories/demo", json={"secret_name": _SPARE})
    for row in (named, agreeing, unchanged):
        assert row.status_code in (200, 201), row.text
        assert "Deprecation" not in row.headers
        assert (row.json()["warnings"], row.json()["credential_id"]) == ([], deploy["credential_id"])
    assert _old_field_events(hub) == []

    resolved = hub.client.post(
        "/api/repositories", json=_repository(slug="legacy", repo="legacy", secret_name=FIXTURE_FORGE_SECRET)
    )
    assert resolved.status_code == 201, resolved.text
    assert resolved.headers["Deprecation"] == "true"
    body = RepositorySummary.model_validate(resolved.json())
    assert (body.credential_slug, body.secret_name) == (FIXTURE_FORGE_SECRET, FIXTURE_FORGE_SECRET)
    (warning,) = body.warnings
    assert (warning.field, warning.forge_slug) == ("secret_name", "ghe")
    assert f"credential {FIXTURE_FORGE_SECRET}" in warning.message
    (event,) = _old_field_events(hub)
    assert (event["severity"], event["field"], event["record"], event["credential_id"]) == (
        "warning",
        "secret_name",
        "repository legacy",
        body.credential_id,
    )
    assert hub.client.get("/api/repositories/legacy").json()["warnings"] == []

    source = hub.client.post("/api/work-sources", json=_source(secret=_SPARE))
    assert source.status_code == 201, source.text
    assert source.headers["Deprecation"] == "true"
    parsed = WorkSourceSummary.model_validate(source.json())
    assert (parsed.credential_id, [w.field for w in parsed.warnings]) == (deploy["credential_id"], ["secret"])
    assert [e["record"] for e in _old_field_events(hub)] == ["repository legacy", "work source issues"]


def test_a_body_written_before_credential_validates_and_its_response_parses(hub: HubHarness) -> None:
    created = hub.client.post("/api/repositories", json=_repository(secret_name=FIXTURE_FORGE_SECRET))
    assert created.status_code == 201, created.text
    summary = RepositorySummary.model_validate(created.json())
    assert summary.secret_name == FIXTURE_FORGE_SECRET
    edited = hub.client.patch("/api/repositories/demo", json={"base_branch": "trunk"})
    assert (edited.status_code, edited.json()["base_branch"]) == (200, "trunk")
    source = hub.client.post("/api/work-sources", json=_source(secret=FIXTURE_FORGE_SECRET))
    assert source.status_code == 201, source.text
    assert WorkSourceSummary.model_validate(source.json()).credential_id == summary.credential_id


def test_a_secret_edit_on_a_credential_is_written_to_its_referrers(hub: HubHarness) -> None:
    _credential(hub)
    assert hub.client.post("/api/repositories", json=_repository(credential="deploy")).status_code == 201
    assert hub.client.post("/api/work-sources", json=_source(credential="deploy")).status_code == 201

    assert hub.client.patch("/api/credentials/deploy", json={"secret": "third-token"}).status_code == 200

    assert hub.client.get("/api/repositories/demo").json()["secret_name"] == "third-token"
    assert hub.client.get("/api/work-sources/issues").json()["secret"] == "third-token"
    referrers = hub.client.get("/api/credentials/deploy").json()["referrers"]
    assert [(r["kind"], r["label"]) for r in referrers] == [("repository", "demo"), ("work_source", "issues")]


def test_credential_reads_and_writes_require_their_permissions(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth", repositories=(), link=False)
    admin = {"Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='ada', role=Role.ADMIN))}"}
    contributor = {
        "Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='con', role=Role.CONTRIBUTOR))}"
    }
    guest = {"Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='gus', role=Role.GUEST))}"}
    secret = {"name": FIXTURE_FORGE_SECRET, "value": "v"}
    assert hub.client.post("/api/secrets", json=secret, headers=admin).status_code == 201
    forge = {"slug": "ghe", "kind": "github", "api_base": "https://ghe.example"}
    assert hub.client.post("/api/forges", json=forge, headers=admin).status_code == 201
    body = {"slug": "deploy", "forge": "ghe", "binding": "github.token", "secret": FIXTURE_FORGE_SECRET}
    assert hub.client.post("/api/credentials", json=body).status_code == 401
    assert hub.client.post("/api/credentials", json=body, headers=contributor).status_code == 403
    assert hub.client.post("/api/credentials", json=body, headers=admin).status_code == 201
    for path in ("/api/credentials", "/api/credentials/deploy"):
        assert hub.client.get(path, headers=guest).status_code == 200
        assert hub.client.get(path).status_code == 401
    assert hub.client.patch("/api/credentials/deploy", json={}, headers=contributor).status_code == 403
    for verb in ("retire", "enable"):
        assert hub.client.post(f"/api/credentials/deploy/{verb}", headers=contributor).status_code == 403
