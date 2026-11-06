from pathlib import Path

import pytest
import sqlalchemy as sa
from pydantic import BaseModel

from blizzard.hub.store import schema
from blizzard.wire.auth import LoginDisabledProblem, LoginProviderUnknownProblem
from tests.support.oauth_login_double import FakeOAuthProvider
from tests.support.refusal_observation import RefusalObservation, refusal_mismatches, unobserved_variants
from tests.support.scaffolding import build_hub, hub_scoped


@pytest.mark.parametrize(
    ("auth_mode", "name", "model"),
    [("none", "github", LoginDisabledProblem), ("oauth", "missing", LoginProviderUnknownProblem)],
)
def test_login_authorize_refusals_match_openapi_without_creating_auth_records(
    tmp_path: Path, auth_mode: str, name: str, model: type[BaseModel]
) -> None:
    provider = FakeOAuthProvider(name="github")
    hub = build_hub(tmp_path, auth_mode=auth_mode, oauth_providers={"github": provider})
    assert hub.app is not None
    tables = (schema.users, schema.identities, schema.sessions)
    with hub.engine.connect() as conn:
        before = [conn.execute(hub_scoped(sa.select(table))).all() for table in tables]

    response = hub.client.get(f"/api/auth/{name}/authorize", follow_redirects=False)

    assert response.status_code == 404, response.text
    path = "/api/auth/{name}/authorize"
    observation = RefusalObservation("get", path, 404, response.headers["content-type"], model, response.json())
    spec = hub.app.openapi()
    assert refusal_mismatches(spec, observation) == []
    assert unobserved_variants(spec, "get", path, 404, "application/problem+json", {model}) == (
        {LoginDisabledProblem.__name__, LoginProviderUnknownProblem.__name__} - {model.__name__}
    )
    with hub.engine.connect() as conn:
        assert [conn.execute(hub_scoped(sa.select(table))).all() for table in tables] == before
    assert provider.authorize_calls == []
    assert "bz_session" not in response.cookies
