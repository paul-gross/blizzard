"""``blizzard hub routine edit`` (component tier) — a real ``build_hosted_app`` the CLI's
commands' requests are routed into, so ``--clear`` meets the hub's own refusal of ``null`` on a list
field and acceptance of ``[]``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from blizzard.hub.cli import hub as hub_group
from tests.config_inputs import build_hosted_hub_app, init_hub
from tests.worker_http import Bound, bind_stubs

pytestmark = pytest.mark.component

_HUB = "http://hub.local:8421"
_GRAPH = """
name: alpha
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
"""


_bound: dict[str, Bound] = {}
"""The collaborators the ``client`` fixture bound over its in-process app, for the invokers below."""


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    config = init_hub(tmp_path / "hub")
    with TestClient(build_hosted_hub_app(config)) as c:

        def forward(method: str, url: str, *, timeout: float, **kwargs):
            return c.request(method.upper(), urlsplit(url).path, **kwargs)

        _bound["hub"] = bind_stubs(request=forward)
        yield c


def _edit(routine_id: str, *args: str):
    return _bound["hub"].runner.invoke(hub_group, ["routine", "edit", routine_id, *args], env={"BZ_HUB_URL": _HUB})


def test_clear_empties_effort_model_and_harnesses_and_leaves_the_rest(client: TestClient) -> None:
    assert client.post("/api/graphs", json={"definition_yaml": _GRAPH}).status_code == 201
    created = client.post(
        "/api/routines",
        json={
            "name": "nightly",
            "graph_name": "alpha",
            "default_scope_slug": "blizzard",
            "default_model": ["m1"],
            "default_effort": "high",
            "default_harnesses": ["claude_code"],
        },
    )
    assert created.status_code == 201, created.text
    routine_id = created.json()["routine_id"]

    cleared = _edit(routine_id, "--clear", "effort", "--clear", "model", "--clear", "harnesses")
    assert cleared.exit_code == 0, cleared.output
    shown = client.get(f"/api/routines/{routine_id}").json()
    assert shown["default_effort"] is None
    assert shown["default_model"] == []
    assert shown["default_harnesses"] == []
    assert shown["name"] == "nightly"
    assert shown["graph_name"] == "alpha"

    untouched = _edit(routine_id, "--graph", "alpha")
    assert untouched.exit_code == 0, untouched.output
    assert client.get(f"/api/routines/{routine_id}").json()["default_effort"] is None


def test_an_unknown_routine_is_answered_by_the_patch_itself(client: TestClient) -> None:
    result = _edit("rtn_ghost", "--graph", "alpha")

    assert result.exit_code != 0
    assert "unknown routine rtn_ghost" in result.output
