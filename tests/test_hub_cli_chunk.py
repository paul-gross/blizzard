"""``blizzard hub chunk migrate`` (unit tier) — a pure client of ``PATCH
/api/chunks/{id}``, driven here with ``httpx`` stubbed (issue #124).
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


def _patch_response(chunk_id: str, intended_migration: object | None) -> _FakeResponse:
    return _FakeResponse(
        202,
        {
            "chunk_id": chunk_id,
            "graph_id": "gr_1",
            "model": "sonnet",
            "intended_migration": intended_migration,
        },
    )


@pytest.mark.unit
def test_migrate_forced_sends_to_graph_and_node(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _patch_response("ch_1", {"mode": "forced", "graph_id": "gr_2", "graph_name": "beta", "node_name": "n2"})

    monkeypatch.setattr(hub_cli.httpx, "patch", fake_patch)
    result = CliRunner().invoke(
        hub_group,
        ["chunk", "migrate", "ch_1", "--to-graph", "beta", "--node", "n2"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        ("http://hub.local:8421/api/chunks/ch_1", {"intended_migration": {"to_graph": "beta", "node": "n2"}})
    ]
    assert "beta" in result.output
    assert "n2" in result.output


@pytest.mark.unit
def test_migrate_auto_omits_node(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _patch_response("ch_1", {"mode": "auto", "graph_id": "gr_2", "graph_name": "beta", "node_name": None})

    monkeypatch.setattr(hub_cli.httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "migrate", "ch_1", "--to-graph", "beta"])

    assert result.exit_code == 0, result.output
    assert calls == [("http://127.0.0.1:8421/api/chunks/ch_1", {"intended_migration": {"to_graph": "beta"}})]
    assert "auto-migrate" in result.output
    assert "beta" in result.output


@pytest.mark.unit
def test_migrate_cancel_sends_null_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _patch_response("ch_1", None)

    monkeypatch.setattr(hub_cli.httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "migrate", "ch_1", "--cancel"])

    assert result.exit_code == 0, result.output
    assert calls == [("http://127.0.0.1:8421/api/chunks/ch_1", {"intended_migration": None})]
    assert "cleared" in result.output


@pytest.mark.unit
def test_migrate_cancel_conflicts_with_to_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        raise AssertionError("must not call the API when the flags conflict")

    monkeypatch.setattr(hub_cli.httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "migrate", "ch_1", "--cancel", "--to-graph", "beta"])

    assert result.exit_code != 0
    assert "--cancel" in result.output


@pytest.mark.unit
def test_migrate_cancel_conflicts_with_node(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        raise AssertionError("must not call the API when the flags conflict")

    monkeypatch.setattr(hub_cli.httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "migrate", "ch_1", "--cancel", "--node", "n2"])

    assert result.exit_code != 0
    assert "--cancel" in result.output


@pytest.mark.unit
def test_migrate_without_to_graph_or_cancel_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        raise AssertionError("must not call the API without --to-graph or --cancel")

    monkeypatch.setattr(hub_cli.httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "migrate", "ch_1"])

    assert result.exit_code != 0
    assert "--to-graph" in result.output


@pytest.mark.unit
def test_migrate_maps_409_to_the_server_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "chunk is already pinned to graph gr_2"})

    monkeypatch.setattr(hub_cli.httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "migrate", "ch_1", "--to-graph", "beta"])

    assert result.exit_code != 0
    assert "already pinned to graph gr_2" in result.output


@pytest.mark.unit
def test_migrate_maps_422_to_the_server_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(422, {"detail": "node must not be blank"})

    monkeypatch.setattr(hub_cli.httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "migrate", "ch_1", "--to-graph", "beta", "--node", " "])

    assert result.exit_code != 0
    assert "node must not be blank" in result.output


@pytest.mark.unit
def test_migrate_maps_404_to_the_server_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {"detail": "unknown chunk ch_ghost"})

    monkeypatch.setattr(hub_cli.httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "migrate", "ch_ghost", "--to-graph", "beta"])

    assert result.exit_code != 0
    assert "unknown chunk ch_ghost" in result.output


@pytest.mark.unit
def test_migrate_json_prints_the_raw_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "model": "sonnet",
        "intended_migration": {"mode": "auto", "graph_id": "gr_2", "graph_name": "beta", "node_name": None},
    }

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(202, payload)

    monkeypatch.setattr(hub_cli.httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "migrate", "ch_1", "--to-graph", "beta", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload
