"""``--note`` on ``blizzard hub decision resolve``, ``chunk requeue`` and ``chunk restart`` (unit tier, client
stubbed): the flag sends ``note``, and leaving it out sends the body the verb always sent."""

from __future__ import annotations

from typing import Any

import httpx2
import pytest

from blizzard.hub.cli import hub as hub_group
from tests.worker_http import Bound, bind_stubs

pytestmark = pytest.mark.unit

_RESOLVED = {"decision_id": "dec_1", "choice": "pass", "resolved_by": "operator"}
_SUMMARY = {"chunk_id": "ch_1", "status": "ready", "current_node_name": "build"}


def _posting(status: int, body: object) -> tuple[Bound, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> httpx2.Response:
        calls.append({"url": url, "json": kwargs.get("json")})
        return httpx2.Response(status, json=body, request=httpx2.Request("POST", url))

    return bind_stubs(post=fake_post), calls


@pytest.mark.parametrize(
    ("flags", "sent"),
    [
        ([], {"choice": "pass", "resolved_by": "operator"}),
        (["--note", "ship it"], {"choice": "pass", "resolved_by": "operator", "note": "ship it"}),
    ],
)
def test_resolve_sends_the_note_only_when_given(flags: list[str], sent: dict[str, str]) -> None:
    bound, calls = _posting(200, _RESOLVED)

    result = bound.runner.invoke(hub_group, ["decision", "resolve", "dec_1", "pass", *flags])

    assert result.exit_code == 0, result.output
    assert [c["json"] for c in calls] == [sent]


@pytest.mark.parametrize(("flags", "sent"), [([], None), (["--note", "retry once"], {"note": "retry once"})])
def test_requeue_sends_a_body_only_with_a_note(flags: list[str], sent: dict[str, str] | None) -> None:
    bound, calls = _posting(202, _SUMMARY)

    result = bound.runner.invoke(hub_group, ["chunk", "requeue", "ch_1", *flags])

    assert result.exit_code == 0, result.output
    assert [(c["url"].rsplit("/api", 1)[1], c["json"]) for c in calls] == [("/chunks/ch_1/requeues", sent)]


@pytest.mark.parametrize(
    ("flags", "sent"),
    [
        ([], {"node": None, "to_graph": None, "by": "operator"}),
        (["--note", "clean slate"], {"node": None, "to_graph": None, "by": "operator", "note": "clean slate"}),
    ],
)
def test_restart_sends_the_note_only_when_given(flags: list[str], sent: dict[str, object]) -> None:
    bound, calls = _posting(202, _SUMMARY)

    result = bound.runner.invoke(hub_group, ["chunk", "restart", "ch_1", *flags])

    assert result.exit_code == 0, result.output
    assert [c["json"] for c in calls] == [sent]
