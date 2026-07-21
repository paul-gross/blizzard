"""Graphs router ``reject_runner_principal`` guard (issue #104, S5), component tier.

``graphs.py`` was the one operator router still missing
``dependencies=[Depends(reject_runner_principal)]`` — every other operator router
(``queue.py``, ``chunks.py``, ``decisions.py``, ``questions.py``, ``runners.py``)
already carries it. This closes that gap: a runner's bearer token is rejected under
``enforce`` the same way it is on every other operator verb, while an anonymous call
still succeeds. Graph routes themselves are untouched (still immutable
POST-new-version)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub

pytestmark = pytest.mark.component

_GRAPH_YAML = """
name: tiny
entry: build
nodes:
  build:
    executor: runner
    prompt: do the work
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
        fail:
          description: it does not
          to: build
    retries:
      max: 1
      exhausted: escalate
"""


def test_anonymous_mint_still_succeeds(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML})
    assert resp.status_code == 201, resp.text


def test_runner_bearer_token_is_rejected_on_mint_graph(tmp_path: Path) -> None:
    from blizzard.hub.config import RUNNER_AUTH_ENFORCE
    from tests.test_fleet_auth import _bearer, _seed_enrolled

    token = _seed_enrolled(tmp_path)
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)

    resp = hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}, headers=_bearer(token))
    assert resp.status_code == 403


def test_runner_bearer_token_is_rejected_on_list_and_get_graph(tmp_path: Path) -> None:
    from blizzard.hub.config import RUNNER_AUTH_ENFORCE
    from tests.test_fleet_auth import _bearer, _seed_enrolled

    token = _seed_enrolled(tmp_path)
    warn_hub = build_hub(tmp_path)
    graph_id = warn_hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}).json()["graph_id"]

    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    assert hub.client.get("/api/graphs", headers=_bearer(token)).status_code == 403
    assert hub.client.get(f"/api/graphs/{graph_id}", headers=_bearer(token)).status_code == 403


def test_runner_bearer_token_under_warn_is_logged_and_proceeds(tmp_path: Path) -> None:
    """``warn`` (the default) is a rollout brake, not a partition — matches the rest
    of the operator surface's rollout posture."""
    from tests.test_fleet_auth import _bearer, _seed_enrolled

    token = _seed_enrolled(tmp_path)
    hub = build_hub(tmp_path)  # warn, the default

    resp = hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}, headers=_bearer(token))
    assert resp.status_code == 201, resp.text
