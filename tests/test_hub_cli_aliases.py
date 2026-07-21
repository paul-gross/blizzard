"""The hidden top-level aliases every pre-#104 flat verb still resolves to (issue
#104) — each one must (a) still work (delegate to its group successor and produce the
same effect) and (b) warn on stderr naming the successor spelling, since release-tier
e2e/journey shell the old spellings verbatim (``bzh:sweep-release-only-tiers``). Also
covers the uniform ``--url``-is-a-deprecated-alias-of-``--hub-url`` warning, and the
``--json`` output shape on a representative read verb and a representative write verb.
"""

from __future__ import annotations

import json

import httpx
import pytest
from click.testing import CliRunner

import blizzard.hub.cli as hub_cli
from blizzard.hub.cli import hub as hub_group


class _FakeResponse:
    def __init__(self, status_code: int, payload: object | None = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)  # type: ignore[arg-type]


pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Hidden aliases — each warns on stderr and still performs the action.
# --------------------------------------------------------------------------- #


def test_ingest_alias_warns_and_still_ingests(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(201, {"chunk_id": "ch_new"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["ingest", "blizzard:8"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert "blizzard hub ingest" in result.stderr
    assert "blizzard hub chunk ingest" in result.stderr
    assert calls == [("http://hub.local:8421/api/chunks", {"tokens": ["blizzard:8"]})]
    assert "ch_new" in result.output


def test_promote_alias_warns_and_still_promotes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(202, {"chunk_id": "ch_42", "status": "ready"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["promote", "ch_42"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert "blizzard hub promote" in result.stderr
    assert "blizzard hub chunk promote" in result.stderr
    assert calls == ["http://hub.local:8421/api/chunks/ch_42/promote"]
    assert "promoted ch_42" in result.output


def test_requeue_alias_warns_and_still_requeues(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(202, {"chunk_id": "ch_42"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["requeue", "ch_42"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert "blizzard hub requeue" in result.stderr
    assert "blizzard hub chunk requeue" in result.stderr
    assert calls == ["http://hub.local:8421/api/chunks/ch_42/requeues"]
    assert "requeued ch_42" in result.output


def test_detach_alias_warns_and_still_detaches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(202, {"chunk_id": "ch_42"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["detach", "ch_42"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert "blizzard hub detach" in result.stderr
    assert "blizzard hub chunk detach" in result.stderr
    assert calls == ["http://hub.local:8421/api/chunks/ch_42/detach"]
    assert "detached ch_42" in result.output


def test_stop_alias_warns_and_still_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"chunk_id": "ch_42"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group, ["stop", "ch_42", "--by", "alice"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    assert "blizzard hub stop" in result.stderr
    assert "blizzard hub chunk stop" in result.stderr
    assert calls == [("http://hub.local:8421/api/chunks/ch_42/stop", {"by": "alice"})]
    assert "stopped ch_42" in result.output


def test_pause_chunk_alias_warns_and_still_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"chunk_id": "ch_42"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group, ["pause-chunk", "ch_42", "--by", "alice"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    assert "blizzard hub pause-chunk" in result.stderr
    assert "blizzard hub chunk pause" in result.stderr
    assert calls == [("http://hub.local:8421/api/chunks/ch_42/pause", {"by": "alice"})]
    assert "paused ch_42" in result.output


def test_resume_chunk_alias_warns_and_still_resumes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"chunk_id": "ch_42"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group, ["resume-chunk", "ch_42", "--by", "alice"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    assert "blizzard hub resume-chunk" in result.stderr
    assert "blizzard hub chunk resume" in result.stderr
    assert calls == [("http://hub.local:8421/api/chunks/ch_42/resume", {"by": "alice"})]
    assert "resumed ch_42" in result.output


def test_pause_alias_warns_and_still_pauses_a_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(200, {"runner_id": "r1", "hub_paused": True, "locally_paused": False})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["pause", "r1"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert "blizzard hub pause" in result.stderr
    assert "blizzard hub runner pause" in result.stderr
    assert calls == [("http://hub.local:8421/api/runners/r1/pause", {"by": "operator"})]
    assert "runner r1 is now paused" in result.output


def test_resume_alias_warns_and_still_resumes_a_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(200, {"runner_id": "r1", "hub_paused": False, "locally_paused": False})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["resume", "r1"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert "blizzard hub resume" in result.stderr
    assert "blizzard hub runner resume" in result.stderr
    assert calls == [("http://hub.local:8421/api/runners/r1/resume", {"by": "operator"})]
    assert "runner r1 is now running" in result.output


def test_decisions_alias_warns_and_still_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(
            200, {"decisions": [{"decision_id": "dc_1", "chunk_id": "ch_1", "node_name": "review", "choices": []}]}
        )

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["decisions"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert "blizzard hub decisions" in result.stderr
    assert "blizzard hub decision list" in result.stderr
    assert calls == ["http://hub.local:8421/api/decisions"]
    assert "dc_1" in result.output


def test_decide_alias_warns_and_still_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(200, {"decision_id": "dc_1", "choice": "pass", "resolved_by": "operator"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["decide", "dc_1", "pass"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert "blizzard hub decide" in result.stderr
    assert "blizzard hub decision resolve" in result.stderr
    assert calls == [
        ("http://hub.local:8421/api/decisions/dc_1/resolutions", {"choice": "pass", "resolved_by": "operator"})
    ]
    assert "resolved: pass" in result.output


def test_answer_alias_warns_and_still_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(201, {"won": True, "question_id": "qn_1", "answer": "42", "answered_by": "operator"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["answer", "qn_1", "42"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert "blizzard hub answer" in result.stderr
    assert "blizzard hub question answer" in result.stderr
    assert calls == [("http://hub.local:8421/api/questions/qn_1/answers", {"answer": "42", "answered_by": "operator"})]
    assert "answered qn_1" in result.output


# --------------------------------------------------------------------------- #
# `--url` is a deprecated alias of `--hub-url`, uniform everywhere.
# --------------------------------------------------------------------------- #


def test_url_flag_warns_and_still_feeds_the_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(202, {"chunk_id": "ch_42"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["chunk", "promote", "ch_42", "--url", "http://legacy:8421"])

    assert result.exit_code == 0, result.output
    assert "--url is deprecated" in result.stderr
    assert "--hub-url" in result.stderr
    assert calls == ["http://legacy:8421/api/chunks/ch_42/promote"]


def test_hub_url_flag_wins_over_url_when_both_given(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(202, {"chunk_id": "ch_42"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group,
        ["chunk", "promote", "ch_42", "--hub-url", "http://current:8421", "--url", "http://legacy:8421"],
    )

    assert result.exit_code == 0, result.output
    assert "--url is deprecated" in result.stderr
    assert calls == ["http://current:8421/api/chunks/ch_42/promote"]


# --------------------------------------------------------------------------- #
# `--json` — a representative read verb and a representative write verb.
# --------------------------------------------------------------------------- #


def test_json_on_a_read_verb_prints_the_raw_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [{"chunk_id": "ch_1", "status": "ready", "current_node_id": None, "model": "sonnet", "cost": {}}]

    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, payload)

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["chunk", "list", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload


def test_json_on_a_write_verb_prints_the_raw_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"chunk_id": "ch_42", "status": "ready", "graph_id": "gr_1", "model": "sonnet"}

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(202, payload)

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["chunk", "promote", "ch_42", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload
