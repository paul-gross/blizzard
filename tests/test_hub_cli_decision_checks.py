"""``blizzard hub decision list`` and ``resolve`` surface a gate's checks and an override (unit tier, client
stubbed)."""

from __future__ import annotations

import httpx
import pytest

from blizzard.hub.cli import hub as hub_group
from tests.worker_http import bind_stubs

pytestmark = pytest.mark.unit


def _ok(body: object) -> httpx.Response:
    return httpx.Response(200, json=body, request=httpx.Request("GET", "http://hub.test"))


def _decision(checks: list[dict[str, object]]) -> dict[str, object]:
    return {
        "decision_id": "dec_1",
        "chunk_id": "ch_1",
        "node_name": "build",
        "choices": [{"name": "pass"}, {"name": "fail"}],
        "check_results": checks,
    }


def test_the_list_summarizes_a_decisions_red_checks() -> None:
    checks = [{"command": "a", "passed": True}, {"command": "b", "passed": False}]
    body = {"decisions": [_decision(checks), {**_decision([]), "decision_id": "dec_2"}]}
    bound = bind_stubs(get=lambda url, **_: _ok(body))

    result = bound.runner.invoke(hub_group, ["decision", "list"])

    assert result.exit_code == 0, result.output
    assert "dec_1  chunk=ch_1  node=build  choices=[pass, fail]  checks=1/2 red" in result.output
    assert "dec_2  chunk=ch_1  node=build  choices=[pass, fail]\n" in result.output


@pytest.mark.parametrize(("overrode", "suffix"), [(True, " — overrode red checks"), (False, "")])
def test_resolve_reports_an_override(overrode: bool, suffix: str) -> None:
    body = {"decision_id": "dec_1", "choice": "pass", "resolved_by": "operator", "overrode_red_checks": overrode}
    bound = bind_stubs(post=lambda url, **_: _ok(body))

    result = bound.runner.invoke(hub_group, ["decision", "resolve", "dec_1", "pass"])

    assert result.exit_code == 0, result.output
    assert f"decision dec_1 resolved: pass (by operator){suffix}\n" in result.output
