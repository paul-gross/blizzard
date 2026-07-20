"""``blizzard hub graph list|retire|enable|upload`` (unit tier) — pure clients of the
graph lifecycle and mint endpoints, driven here with ``httpx`` stubbed (issue #101,
issue #123).
"""

from __future__ import annotations

from pathlib import Path

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


@pytest.mark.unit
def test_graph_list_prints_each_row(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(
            200,
            [
                {"graph_id": "gr_new", "name": "alpha", "effective": True, "retired": False, "created_at": "t1"},
                {"graph_id": "gr_old", "name": "alpha", "effective": False, "retired": True, "created_at": "t0"},
            ],
        )

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["graph", "list"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert calls == ["http://hub.local:8421/api/graphs"]
    assert "gr_new" in result.output
    assert "effective" in result.output
    assert "gr_old" in result.output
    assert "retired" in result.output


@pytest.mark.unit
def test_graph_list_on_no_graphs_prints_a_friendly_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, [])

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["graph", "list"])

    assert result.exit_code == 0, result.output
    assert "no graphs minted yet" in result.output


@pytest.mark.unit
def test_graph_retire_posts_to_the_retire_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"graph_id": "gr_1", "retired": True})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group, ["graph", "retire", "gr_1", "--by", "paul"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    assert calls == [("http://hub.local:8421/api/graphs/gr_1/retire", {"by": "paul"})]
    assert "retired" in result.output


@pytest.mark.unit
def test_graph_enable_posts_to_the_enable_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"graph_id": "gr_1", "retired": False})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["graph", "enable", "gr_1"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert calls == [("http://hub.local:8421/api/graphs/gr_1/enable", {"by": "operator"})]
    assert "enabled" in result.output


@pytest.mark.unit
def test_graph_retire_maps_an_unknown_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["graph", "retire", "gr_ghost"])

    assert result.exit_code != 0
    assert "gr_ghost" in result.output


_UPLOAD_GRAPH_YAML = """
name: tiny
entry: build
nodes:
  build:
    executor: runner
    prompt: ./prompts/build.md
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

_PROMPT_PROSE = "Build the thing with great care."


def _write_graph_with_prompt_ref(tmp_path: Path) -> Path:
    graph_path = tmp_path / "graph.yaml"
    graph_path.write_text(_UPLOAD_GRAPH_YAML)
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "build.md").write_text(_PROMPT_PROSE)
    return graph_path


@pytest.mark.unit
def test_graph_upload_posts_the_prompt_inlined(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    graph_path = _write_graph_with_prompt_ref(tmp_path)
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(201, {"graph_id": "gr_new", "warnings": []})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group, ["graph", "upload", str(graph_path)], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    url, body = calls[0]
    assert url == "http://hub.local:8421/api/graphs"
    assert isinstance(body, dict)
    posted_yaml = body["definition_yaml"]
    assert _PROMPT_PROSE in posted_yaml
    assert "./prompts/build.md" not in posted_yaml


@pytest.mark.unit
def test_graph_upload_prints_the_minted_graph_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    graph_path = _write_graph_with_prompt_ref(tmp_path)

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(201, {"graph_id": "gr_new", "warnings": []})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["graph", "upload", str(graph_path)])

    assert result.exit_code == 0, result.output
    assert "gr_new" in result.output


@pytest.mark.unit
def test_graph_upload_surfaces_mint_warnings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    graph_path = _write_graph_with_prompt_ref(tmp_path)

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(201, {"graph_id": "gr_new", "warnings": ["node build has no incoming edges"]})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["graph", "upload", str(graph_path)])

    assert result.exit_code == 0, result.output
    assert "node build has no incoming edges" in result.output


@pytest.mark.unit
def test_graph_upload_maps_a_validation_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    graph_path = _write_graph_with_prompt_ref(tmp_path)

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(422, {"ok": False, "errors": ["entry node 'build' not found"], "warnings": []})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["graph", "upload", str(graph_path)])

    assert result.exit_code != 0
    assert "entry node 'build' not found" in result.output
