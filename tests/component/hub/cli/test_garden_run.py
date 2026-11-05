"""``blizzard hub run list|get`` against a real, wired app (``tests/component/hub/cli/test_analytics_events.py``'s relay
shape)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import httpx2

from blizzard.foundation.run_mode import RunMode
from blizzard.hub.cli import hub as hub_group
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.routines import Routine
from blizzard.hub.domain.garden.scopes import ScopeSlug
from blizzard.hub.domain.graph.model import Graph
from tests.support.scaffolding import HubHarness, build_hub, default_project, project_record
from tests.support.worker_http import Bound, bind_stubs

_CTX = ChangeContext(actor="operator", door=Door.API)


def _default_graph(hub: HubHarness) -> Graph:
    return hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )


def _routine(hub: HubHarness) -> Routine:
    graph = _default_graph(hub)
    return hub.services.routine_authoring.create(
        ctx=_CTX,
        name="gardening",
        graph_name=graph.name,
        default_scope_slug=ScopeSlug.parse("blizzard"),
        default_model=None,
        default_effort=None,
        project=project_record(hub, default_project(hub.engine)),
    )


_HUB_URL = "http://hub.local:8421"


def _relay(hub: HubHarness) -> Bound:
    def fake_get(url: str, *, params: dict[str, str] | None = None, timeout: float) -> httpx2.Response:
        return hub.client.get(url, params=params)

    return bind_stubs(get=fake_get)


def test_run_list_and_show_work_end_to_end_against_a_real_app(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine = _routine(hub)
    scope = hub.services.scopes.get(routine.project_id, routine.default_scope_slug)
    assert scope is not None
    result = hub.services.routine_run.run(
        routine,
        scope=scope,
        mode=RunMode.FULL,
        note=None,
        author=WorkItemAuthor.user("usr_1"),
    )
    hub.promote(result.chunk_id)
    hub.clock.advance(timedelta(hours=1))
    bound = _relay(hub)

    list_result = bound.runner.invoke(hub_group, ["run", "list"], env={"BZ_HUB_URL": _HUB_URL})

    assert list_result.exit_code == 0, list_result.output
    assert result.chunk_id in list_result.output

    show_result = bound.runner.invoke(hub_group, ["run", "get", result.chunk_id], env={"BZ_HUB_URL": _HUB_URL})

    assert show_result.exit_code == 0, show_result.output
    assert result.chunk_id in show_result.output
    assert "outcome: ready" in show_result.output
