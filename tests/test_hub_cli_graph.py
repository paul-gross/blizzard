"""``blizzard hub graph list|show|retire|enable|mint|sync`` (unit tier) — pure clients of
the graph lifecycle, mint, and reconciliation endpoints, driven here with ``httpx``
stubbed (issue #101, issue #104, issue #123, issue #146). ``mint`` inlines referenced
prompt files and accepts stdin (``-``); ``sync`` is the deploy verb, reconciling the
**hub's** packaged set.
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


@pytest.mark.unit
def test_graph_show_prints_the_reified_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            {
                "graph_id": "gr_1",
                "name": "alpha",
                "entry_node_id": "nd_1",
                "enabled": True,
                "retired": False,
                "nodes": [{"node_id": "nd_1", "name": "build", "executor": "runner"}],
                "edges": [{"from_node_id": "nd_1", "choice_id": "pass", "to_node_name": "done"}],
                "warnings": [],
            },
        )

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["graph", "show", "gr_1"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert "gr_1" in result.output
    assert "build" in result.output
    assert "done" in result.output


@pytest.mark.unit
def test_graph_show_surfaces_the_baked_artifact_names_only_under_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """The read-back for what a mint actually baked, and its one working form: the human
    rendering is nodes and edges, so a graph's `artifacts:` names — and the authored order
    a reorder-only edit silently leaves alone — reach an operator only through ``--json``."""
    body = {
        "graph_id": "gr_1",
        "name": "alpha",
        "entry_node_id": "nd_1",
        "retired": False,
        "nodes": [{"node_id": "nd_1", "name": "build", "executor": "runner"}],
        "edges": [],
        "artifacts": ["zebra", "apple"],
    }

    monkeypatch.setattr(hub_cli.httpx, "get", lambda url, *, timeout: _FakeResponse(200, body))
    env = {"BZ_HUB_URL": "http://hub.local:8421"}
    plain = CliRunner().invoke(hub_group, ["graph", "show", "gr_1"], env=env)
    as_json = CliRunner().invoke(hub_group, ["graph", "show", "gr_1", "--json"], env=env)

    assert plain.exit_code == 0, plain.output
    assert "zebra" not in plain.output
    assert as_json.exit_code == 0, as_json.output
    assert as_json.output.index("zebra") < as_json.output.index("apple")


@pytest.mark.unit
def test_graph_show_maps_an_unknown_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["graph", "show", "gr_ghost"])

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
def test_graph_mint_posts_the_prompt_inlined(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    graph_path = _write_graph_with_prompt_ref(tmp_path)
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(201, {"graph_id": "gr_new", "warnings": []})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group, ["graph", "mint", str(graph_path)], env={"BZ_HUB_URL": "http://hub.local:8421"}
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
def test_graph_mint_prints_the_minted_graph_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    graph_path = _write_graph_with_prompt_ref(tmp_path)

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(201, {"graph_id": "gr_new", "warnings": []})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["graph", "mint", str(graph_path)])

    assert result.exit_code == 0, result.output
    assert "gr_new" in result.output


@pytest.mark.unit
def test_graph_mint_surfaces_mint_warnings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    graph_path = _write_graph_with_prompt_ref(tmp_path)

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(201, {"graph_id": "gr_new", "warnings": ["node build has no incoming edges"]})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["graph", "mint", str(graph_path)])

    assert result.exit_code == 0, result.output
    assert "node build has no incoming edges" in result.output


@pytest.mark.unit
def test_graph_mint_maps_a_validation_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    graph_path = _write_graph_with_prompt_ref(tmp_path)

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(422, {"ok": False, "errors": ["entry node 'build' not found"], "warnings": []})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["graph", "mint", str(graph_path)])

    assert result.exit_code != 0
    assert "entry node 'build' not found" in result.output


@pytest.mark.unit
def test_graph_mint_validation_failure_also_renders_warnings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A 422's report can carry both errors and warnings — both render, not just errors."""
    graph_path = _write_graph_with_prompt_ref(tmp_path)

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            422,
            {
                "ok": False,
                "errors": ["entry node 'build' not found"],
                "warnings": ["node build has no incoming edges"],
            },
        )

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["graph", "mint", str(graph_path)])

    assert result.exit_code != 0
    assert "entry node 'build' not found" in result.output
    assert "node build has no incoming edges" in result.output


@pytest.mark.unit
def test_graph_mint_a_missing_artifact_file_is_a_click_exception_naming_the_entry(tmp_path: Path) -> None:
    """The loader raises before any HTTP call is made, so this never reaches
    ``httpx.post`` at all — the ``ClickException`` names both the entry and its path, which
    are deliberately unalike so that neither assertion can pass on the other's strength."""
    graph_path = tmp_path / "graph.yaml"
    graph_path.write_text(
        "name: tiny\nentry: build\nartifacts:\n  docket: ./reference-notes.md\n"
        "nodes:\n  build:\n    executor: runner\n    prompt: do the work\n"
        "    judgement:\n      prompt: judge it\n      choices:\n"
        "        pass:\n          description: it works\n          to: done\n"
    )

    result = CliRunner().invoke(hub_group, ["graph", "mint", str(graph_path)])

    assert result.exit_code != 0
    assert "docket" in result.output
    assert str(tmp_path / "reference-notes.md") in result.output


@pytest.mark.unit
def test_graph_mint_reads_the_definition_from_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """``-`` reads stdin verbatim — no file, no prompt-ref inlining."""
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(201, {"graph_id": "gr_new", "warnings": []})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group,
        ["graph", "mint", "-"],
        input=_UPLOAD_GRAPH_YAML,
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    url, body = calls[0]
    assert url == "http://hub.local:8421/api/graphs"
    assert isinstance(body, dict)
    assert body["definition_yaml"] == _UPLOAD_GRAPH_YAML
    assert "gr_new" in result.output


@pytest.mark.unit
def test_graph_sync_prints_every_outcome_and_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deploy verb (issue #146): one line per packaged graph, minted or not."""
    calls: list[str] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(
            200,
            {
                "ok": True,
                "entries": [
                    {"name": "default", "status": "up-to-date", "graph_id": None, "detail": None},
                    {"name": "bas-dwf", "status": "minted", "graph_id": "gr_new", "detail": "first of its name"},
                ],
            },
        )

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["graph", "sync"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert calls == ["http://hub.local:8421/api/graphs/sync"]
    assert "default: up-to-date" in result.output
    assert "bas-dwf: minted gr_new — first of its name" in result.output


@pytest.mark.unit
def test_graph_sync_exits_non_zero_when_a_packaged_graph_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A per-graph failure is a non-zero exit — but the graphs that did reconcile are
    still reported, since the reconciler does not stop at the first bad one."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            {
                "ok": False,
                "entries": [
                    {"name": "default", "status": "minted", "graph_id": "gr_a", "detail": "first of its name"},
                    {"name": "broken", "status": "failed", "graph_id": None, "detail": "entry node not found"},
                ],
            },
        )

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["graph", "sync"])

    assert result.exit_code != 0
    assert "default: minted gr_a" in result.output
    assert "broken: failed — entry node not found" in result.output
    assert "failed to reconcile" in result.output
