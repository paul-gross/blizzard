"""``--note`` on ``blizzard hub decision resolve``, ``chunk requeue`` and ``chunk restart`` (unit tier, ``httpx``
stubbed): the flag sends ``note``, and leaving it out sends the body the verb always sent."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from blizzard.hub.cli import hub as hub_group

pytestmark = pytest.mark.unit

_RESOLVED = {"decision_id": "dec_1", "choice": "pass", "resolved_by": "operator"}
_SUMMARY = {"chunk_id": "ch_1", "status": "ready", "current_node_name": "build"}


def _posting(monkeypatch: pytest.MonkeyPatch, status: int, body: object) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        calls.append({"url": url, "json": kwargs.get("json")})
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


@pytest.mark.parametrize(
    ("flags", "sent"),
    [
        ([], {"choice": "pass", "resolved_by": "operator"}),
        (["--note", "ship it"], {"choice": "pass", "resolved_by": "operator", "note": "ship it"}),
    ],
)
def test_resolve_sends_the_note_only_when_given(
    monkeypatch: pytest.MonkeyPatch, flags: list[str], sent: dict[str, str]
) -> None:
    calls = _posting(monkeypatch, 200, _RESOLVED)

    result = CliRunner().invoke(hub_group, ["decision", "resolve", "dec_1", "pass", *flags])

    assert result.exit_code == 0, result.output
    assert [c["json"] for c in calls] == [sent]


@pytest.mark.parametrize(("flags", "sent"), [([], None), (["--note", "retry once"], {"note": "retry once"})])
def test_requeue_sends_a_body_only_with_a_note(
    monkeypatch: pytest.MonkeyPatch, flags: list[str], sent: dict[str, str] | None
) -> None:
    calls = _posting(monkeypatch, 202, _SUMMARY)

    result = CliRunner().invoke(hub_group, ["chunk", "requeue", "ch_1", *flags])

    assert result.exit_code == 0, result.output
    assert [(c["url"].rsplit("/api", 1)[1], c["json"]) for c in calls] == [("/chunks/ch_1/requeues", sent)]


@pytest.mark.parametrize(
    ("flags", "sent"),
    [
        ([], {"node": None, "to_graph": None, "by": "operator"}),
        (["--note", "clean slate"], {"node": None, "to_graph": None, "by": "operator", "note": "clean slate"}),
    ],
)
def test_restart_sends_the_note_only_when_given(
    monkeypatch: pytest.MonkeyPatch, flags: list[str], sent: dict[str, object]
) -> None:
    calls = _posting(monkeypatch, 202, _SUMMARY)

    result = CliRunner().invoke(hub_group, ["chunk", "restart", "ch_1", *flags])

    assert result.exit_code == 0, result.output
    assert [c["json"] for c in calls] == [sent]
