"""``blizzard hub graph list|show|retire|enable|follow-latest|mint|sync`` (unit tier) — pure clients of
the graph lifecycle, mint, and reconciliation endpoints, driven here with the client factory
stubbed. ``mint`` inlines referenced
prompt files and accepts stdin (``-``); ``sync`` is the deploy verb, reconciling the
**hub's** packaged set into every open tenant.
"""

from __future__ import annotations

from pathlib import Path

import httpx2
import pytest
from click.testing import CliRunner

from blizzard.hub.cli import hub as hub_group
from tests.worker_http import bind_stubs


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
            raise httpx2.HTTPStatusError("error", request=None, response=None)  # type: ignore[arg-type]


@pytest.mark.unit
def test_graph_list_prints_each_row() -> None:
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

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["graph", "list"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert calls == ["http://hub.local:8421/api/graphs"]
    assert "gr_new" in result.output
    assert "effective" in result.output
    assert "gr_old" in result.output
    assert "retired" in result.output


@pytest.mark.unit
def test_graph_list_on_no_graphs_prints_a_friendly_message() -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, [])

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["graph", "list"])

    assert result.exit_code == 0, result.output
    assert "no graphs minted yet" in result.output


@pytest.mark.unit
def test_graph_retire_posts_to_the_retire_endpoint() -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"graph_id": "gr_1", "retired": True})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["graph", "retire", "gr_1"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert calls == [("http://hub.local:8421/api/graphs/gr_1/retire", {})]
    assert "retired" in result.output


@pytest.mark.unit
@pytest.mark.parametrize(("value", "sent"), [("true", True), ("false", False), ("inherit", None)])
def test_graph_follow_latest_patches_the_graph(value: str, sent: object) -> None:
    calls: list[tuple[str, object]] = []

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(200, {"graph_id": "gr_1", "follow_latest": sent})

    bound = bind_stubs(patch=fake_patch)
    result = bound.runner.invoke(
        hub_group, ["graph", "follow-latest", "gr_1", value], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    assert calls == [("http://hub.local:8421/api/graphs/gr_1", {"follow_latest": sent})]


@pytest.mark.unit
def test_graph_enable_posts_to_the_enable_endpoint() -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"graph_id": "gr_1", "retired": False})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["graph", "enable", "gr_1"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert calls == [("http://hub.local:8421/api/graphs/gr_1/enable", {})]
    assert "enabled" in result.output


@pytest.mark.unit
def test_graph_retire_maps_an_unknown_graph() -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["graph", "retire", "gr_ghost"])

    assert result.exit_code != 0
    assert "gr_ghost" in result.output


@pytest.mark.unit
def test_graph_show_prints_the_reified_graph() -> None:
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

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["graph", "show", "gr_1"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert "gr_1" in result.output
    assert "build" in result.output
    assert "done" in result.output


@pytest.mark.unit
def test_graph_show_renders_session_pools() -> None:
    body = {
        "graph_id": "gr_1",
        "name": "alpha",
        "entry_node_id": "nd_1",
        "retired": False,
        "sessions": [
            {
                "name": "code",
                "model": ["blizzard:basic"],
                "effort": "medium",
                "rotate": {"max_context_tokens": None, "max_transcript_bytes": None, "max_invocations": 30},
                "compaction_window": "150000",
            }
        ],
        "nodes": [{"node_id": "nd_1", "name": "build", "executor": "runner"}],
        "edges": [],
    }

    bound = bind_stubs(get=lambda url, *, timeout: _FakeResponse(200, body))
    result = bound.runner.invoke(hub_group, ["graph", "show", "gr_1"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert "session code" in result.output
    assert "compaction_window=150000" in result.output
    assert "max_invocations=30" in result.output


@pytest.mark.unit
def test_graph_show_renders_a_sessions_harnesses_set() -> None:
    body = {
        "graph_id": "gr_1",
        "name": "alpha",
        "entry_node_id": "nd_1",
        "retired": False,
        "sessions": [
            {
                "name": "code",
                "model": [],
                "effort": None,
                "rotate": None,
                "compaction_window": None,
                "harnesses": ["claude_code", "codex"],
            }
        ],
        "nodes": [{"node_id": "nd_1", "name": "build", "executor": "runner"}],
        "edges": [],
    }

    bound = bind_stubs(get=lambda url, *, timeout: _FakeResponse(200, body))
    result = bound.runner.invoke(hub_group, ["graph", "show", "gr_1"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert "harnesses=claude_code,codex" in result.output


@pytest.mark.unit
def test_graph_show_surfaces_the_baked_artifact_names_only_under_json() -> None:
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

    bound = bind_stubs(get=lambda url, *, timeout: _FakeResponse(200, body))
    env = {"BZ_HUB_URL": "http://hub.local:8421"}
    plain = bound.runner.invoke(hub_group, ["graph", "show", "gr_1"], env=env)
    as_json = bound.runner.invoke(hub_group, ["graph", "show", "gr_1", "--json"], env=env)

    assert plain.exit_code == 0, plain.output
    assert "zebra" not in plain.output
    assert as_json.exit_code == 0, as_json.output
    assert as_json.output.index("zebra") < as_json.output.index("apple")


@pytest.mark.unit
def test_graph_show_maps_an_unknown_graph() -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["graph", "show", "gr_ghost"])

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
def test_graph_mint_posts_the_prompt_inlined(tmp_path: Path) -> None:
    graph_path = _write_graph_with_prompt_ref(tmp_path)
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(201, {"graph_id": "gr_new", "warnings": []})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
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
def test_graph_mint_prints_the_minted_graph_id(tmp_path: Path) -> None:
    graph_path = _write_graph_with_prompt_ref(tmp_path)

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(201, {"graph_id": "gr_new", "warnings": []})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["graph", "mint", str(graph_path)])

    assert result.exit_code == 0, result.output
    assert "gr_new" in result.output


@pytest.mark.unit
def test_graph_mint_surfaces_mint_warnings(tmp_path: Path) -> None:
    graph_path = _write_graph_with_prompt_ref(tmp_path)

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(201, {"graph_id": "gr_new", "warnings": ["node build has no incoming edges"]})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["graph", "mint", str(graph_path)])

    assert result.exit_code == 0, result.output
    assert "node build has no incoming edges" in result.output


@pytest.mark.unit
def test_graph_mint_maps_a_validation_failure(tmp_path: Path) -> None:
    graph_path = _write_graph_with_prompt_ref(tmp_path)

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(422, {"ok": False, "errors": ["entry node 'build' not found"], "warnings": []})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["graph", "mint", str(graph_path)])

    assert result.exit_code != 0
    assert "entry node 'build' not found" in result.output


@pytest.mark.unit
def test_graph_mint_validation_failure_also_renders_warnings(tmp_path: Path) -> None:
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

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["graph", "mint", str(graph_path)])

    assert result.exit_code != 0
    assert "entry node 'build' not found" in result.output
    assert "node build has no incoming edges" in result.output


@pytest.mark.unit
def test_graph_mint_a_missing_artifact_file_is_a_click_exception_naming_the_entry(tmp_path: Path) -> None:
    """The loader raises before any HTTP call is made, so this never reaches
    an HTTP request at all — the ``ClickException`` names both the entry and its path, which
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
def test_graph_mint_reads_the_definition_from_stdin() -> None:
    """``-`` reads stdin verbatim — no file, no prompt-ref inlining."""
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(201, {"graph_id": "gr_new", "warnings": []})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
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
def test_graph_sync_reconciles_every_tenant_and_prints_each_outcome_under_its_tenant() -> None:
    """The deploy verb: one hub-level call, then a block per open tenant with one line per packaged graph."""
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_post(url: str, *, json: object, timeout: float, headers: dict[str, str] | None = None) -> _FakeResponse:
        calls.append((url, headers or {}))
        return _FakeResponse(
            200,
            {
                "ok": True,
                "tenants": [
                    {
                        "tenant_id": "ten_a",
                        "name": "acme",
                        "ok": True,
                        "entries": [{"name": "default", "status": "up-to-date", "graph_id": None, "detail": None}],
                    },
                    {
                        "tenant_id": "ten_b",
                        "name": "bravo",
                        "ok": True,
                        "entries": [
                            {"name": "bas-dwf", "status": "minted", "graph_id": "gr_new", "detail": "first of its name"}
                        ],
                    },
                ],
            },
        )

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["graph", "sync"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert [url for url, _ in calls] == ["http://hub.local:8421/api/admin/graphs/sync"]
    assert all("X-Blizzard-Tenant" not in headers for _, headers in calls)
    assert result.output.splitlines() == [
        "ten_a  acme",
        "  default: up-to-date",
        "ten_b  bravo",
        "  bas-dwf: minted gr_new — first of its name",
    ]


@pytest.mark.unit
def test_graph_sync_exits_non_zero_when_a_packaged_graph_failed_in_any_tenant() -> None:
    """A per-graph failure is a non-zero exit — but every other graph and tenant that did reconcile is still
    reported, since the reconciler stops at no bad graph and no bad tenant."""

    def fake_post(url: str, *, json: object, timeout: float, headers: dict[str, str] | None = None) -> _FakeResponse:
        return _FakeResponse(
            200,
            {
                "ok": False,
                "tenants": [
                    {
                        "tenant_id": "ten_a",
                        "name": "acme",
                        "ok": False,
                        "entries": [
                            {"name": "default", "status": "minted", "graph_id": "gr_a", "detail": "first of its name"},
                            {"name": "broken", "status": "failed", "graph_id": None, "detail": "entry node not found"},
                        ],
                    },
                    {"tenant_id": "ten_b", "name": "bravo", "ok": True, "entries": []},
                ],
            },
        )

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["graph", "sync"])

    assert result.exit_code != 0
    assert "default: minted gr_a" in result.output
    assert "broken: failed — entry node not found" in result.output
    assert "ten_b  bravo\n  no packaged graphs to reconcile" in result.output
    assert "failed to reconcile" in result.output


@pytest.mark.unit
def test_graph_mint_refuses_an_unknown_extension_naming_the_accepted_ones(tmp_path: Path) -> None:
    graph_path = tmp_path / "graph.txt"
    graph_path.write_text("name: tiny\n")

    result = CliRunner().invoke(hub_group, ["graph", "mint", str(graph_path)])

    assert result.exit_code != 0
    for extension in (".yaml", ".yml", ".json"):
        assert extension in result.output


@pytest.mark.unit
def test_graph_mint_renders_a_decode_error_naming_the_key_and_line(tmp_path: Path) -> None:
    graph_path = tmp_path / "graph.yaml"
    graph_path.write_text("name: a\nname: b\n")

    result = CliRunner().invoke(hub_group, ["graph", "mint", str(graph_path)])

    assert result.exit_code != 0
    assert "duplicate key 'name'" in result.output
    assert "line 2" in result.output
