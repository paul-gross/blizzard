"""``blizzard hub chunk migrate`` / ``restart`` / ``set`` / ``get`` (unit tier) — pure
clients of ``PATCH``/``GET /api/chunks/{id}``, driven here with the client stubbed.
"""

from __future__ import annotations

import json

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


def _patch_response(chunk_id: str, intended_migration: object | None) -> _FakeResponse:
    return _FakeResponse(
        202,
        {
            "chunk_id": chunk_id,
            "graph_id": "gr_1",
            "default_model": ["blizzard:basic"],
            "default_effort": "medium",
            "intended_migration": intended_migration,
        },
    )


@pytest.mark.unit
def test_migrate_forced_sends_to_graph_and_node() -> None:
    calls: list[tuple[str, object]] = []

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _patch_response("ch_1", {"mode": "forced", "graph_id": "gr_2", "graph_name": "beta", "node_name": "n2"})

    bound = bind_stubs(patch=fake_patch)
    result = bound.runner.invoke(
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
def test_migrate_auto_omits_node() -> None:
    calls: list[tuple[str, object]] = []

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _patch_response("ch_1", {"mode": "auto", "graph_id": "gr_2", "graph_name": "beta", "node_name": None})

    bound = bind_stubs(patch=fake_patch)
    result = bound.runner.invoke(hub_group, ["chunk", "migrate", "ch_1", "--to-graph", "beta"])

    assert result.exit_code == 0, result.output
    assert calls == [("http://127.0.0.1:8421/api/chunks/ch_1", {"intended_migration": {"to_graph": "beta"}})]
    assert "auto-migrate" in result.output
    assert "beta" in result.output


@pytest.mark.unit
def test_migrate_cancel_sends_null_intent() -> None:
    calls: list[tuple[str, object]] = []

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _patch_response("ch_1", None)

    bound = bind_stubs(patch=fake_patch)
    result = bound.runner.invoke(hub_group, ["chunk", "migrate", "ch_1", "--cancel"])

    assert result.exit_code == 0, result.output
    assert calls == [("http://127.0.0.1:8421/api/chunks/ch_1", {"intended_migration": None})]
    assert "cleared" in result.output


@pytest.mark.unit
def test_migrate_cancel_conflicts_with_to_graph() -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        raise AssertionError("must not call the API when the flags conflict")

    bound = bind_stubs(patch=fake_patch)
    result = bound.runner.invoke(hub_group, ["chunk", "migrate", "ch_1", "--cancel", "--to-graph", "beta"])

    assert result.exit_code != 0
    assert "--cancel" in result.output


@pytest.mark.unit
def test_migrate_cancel_conflicts_with_node() -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        raise AssertionError("must not call the API when the flags conflict")

    bound = bind_stubs(patch=fake_patch)
    result = bound.runner.invoke(hub_group, ["chunk", "migrate", "ch_1", "--cancel", "--node", "n2"])

    assert result.exit_code != 0
    assert "--cancel" in result.output


@pytest.mark.unit
def test_migrate_without_to_graph_or_cancel_is_refused() -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        raise AssertionError("must not call the API without --to-graph or --cancel")

    bound = bind_stubs(patch=fake_patch)
    result = bound.runner.invoke(hub_group, ["chunk", "migrate", "ch_1"])

    assert result.exit_code != 0
    assert "--to-graph" in result.output


@pytest.mark.unit
def test_migrate_maps_409_to_the_server_detail() -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "chunk is already pinned to graph gr_2"})

    bound = bind_stubs(patch=fake_patch)
    result = bound.runner.invoke(hub_group, ["chunk", "migrate", "ch_1", "--to-graph", "beta"])

    assert result.exit_code != 0
    assert "already pinned to graph gr_2" in result.output


@pytest.mark.unit
def test_migrate_maps_422_to_the_server_detail() -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(422, {"detail": "node must not be blank"})

    bound = bind_stubs(patch=fake_patch)
    result = bound.runner.invoke(hub_group, ["chunk", "migrate", "ch_1", "--to-graph", "beta", "--node", " "])

    assert result.exit_code != 0
    assert "node must not be blank" in result.output


@pytest.mark.unit
def test_migrate_maps_404_to_the_server_detail() -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {"detail": "unknown chunk ch_ghost"})

    bound = bind_stubs(patch=fake_patch)
    result = bound.runner.invoke(hub_group, ["chunk", "migrate", "ch_ghost", "--to-graph", "beta"])

    assert result.exit_code != 0
    assert "unknown chunk ch_ghost" in result.output


@pytest.mark.unit
def test_migrate_json_prints_the_raw_response_body() -> None:
    payload = {
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "default_model": ["blizzard:basic"],
        "default_effort": "medium",
        "intended_migration": {"mode": "auto", "graph_id": "gr_2", "graph_name": "beta", "node_name": None},
    }

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(202, payload)

    bound = bind_stubs(patch=fake_patch)
    result = bound.runner.invoke(hub_group, ["chunk", "migrate", "ch_1", "--to-graph", "beta", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == payload


# `chunk restart` (#370, #371) — an event, not the standing intent `migrate` records.
# --------------------------------------------------------------------------- #


def _summary(chunk_id: str, node_name: str) -> _FakeResponse:
    return _FakeResponse(202, {"chunk_id": chunk_id, "status": "running", "current_node_name": node_name})


@pytest.mark.unit
def test_restart_without_a_node_posts_a_null_target() -> None:
    """The common case — restart this step on clean context; the hub resolves the node."""
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _summary("ch_1", "build")

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "restart", "ch_1"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    posted = ("http://hub.local:8421/api/chunks/ch_1/restart", {"node": None, "to_graph": None, "by": "operator"})
    assert calls == [posted]
    assert "build" in result.output


@pytest.mark.unit
def test_restart_sends_the_named_node_and_actor() -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _summary("ch_1", "plan")

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "restart", "ch_1", "--node", "plan", "--by", "ada"])

    assert result.exit_code == 0, result.output
    assert calls[0][1] == {"node": "plan", "to_graph": None, "by": "ada"}


@pytest.mark.unit
def test_restart_sends_the_cross_graph_target_and_names_it_back() -> None:
    """`--to-graph` is the eager move `migrate` only ever schedules (#371)."""
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _summary("ch_1", "build")

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "restart", "ch_1", "--to-graph", "beta"])

    assert result.exit_code == 0, result.output
    assert calls[0][1] == {"node": None, "to_graph": "beta", "by": "operator"}
    assert "beta" in result.output


@pytest.mark.unit
def test_restart_maps_409_to_the_server_detail() -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "node 'deploy' does not exist on graph gr_1"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "restart", "ch_1", "--node", "deploy"])

    assert result.exit_code != 0
    assert "does not exist on graph gr_1" in result.output


# `chunk set --default-model/--default-effort` — the CLI-only surface.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_set_sends_repeated_default_model_flags_as_an_ordered_list() -> None:
    """`--default-model` is repeatable and ORDERED — the CLI must preserve the
    operator's flag order verbatim."""
    calls: list[tuple[str, object]] = []

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(
            202,
            {
                "chunk_id": "ch_1",
                "graph_id": "gr_1",
                "default_model": ["blizzard:advanced", "blizzard:basic"],
                "default_effort": "high",
                "intended_migration": None,
            },
        )

    bound = bind_stubs(patch=fake_patch)
    result = bound.runner.invoke(
        hub_group,
        [
            "chunk",
            "set",
            "ch_1",
            "--default-model",
            "blizzard:advanced",
            "--default-model",
            "blizzard:basic",
            "--default-effort",
            "high",
        ],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "http://hub.local:8421/api/chunks/ch_1",
            {"default_model": ["blizzard:advanced", "blizzard:basic"], "default_effort": "high"},
        )
    ]
    assert "default model → blizzard:advanced, blizzard:basic" in result.output
    assert "default effort → high" in result.output


@pytest.mark.unit
def test_set_omits_a_field_the_operator_did_not_name() -> None:
    """An unnamed field must not reach the body at all."""
    calls: list[object] = []

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse(
            202,
            {
                "chunk_id": "ch_1",
                "graph_id": "gr_1",
                "default_model": [],
                "default_effort": "high",
                "intended_migration": None,
            },
        )

    bound = bind_stubs(patch=fake_patch)
    result = bound.runner.invoke(hub_group, ["chunk", "set", "ch_1", "--default-effort", "high"])

    assert result.exit_code == 0, result.output
    assert calls == [{"default_effort": "high"}]


@pytest.mark.unit
def test_set_with_no_option_at_all_is_a_usage_error() -> None:
    result = CliRunner().invoke(hub_group, ["chunk", "set", "ch_1"])

    assert result.exit_code != 0
    assert "--default-model" in result.output


@pytest.mark.unit
def test_set_sends_repeated_default_harnesses_flags_as_an_ordered_list() -> None:
    calls: list[tuple[str, object]] = []

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(
            202,
            {
                "chunk_id": "ch_1",
                "graph_id": "gr_1",
                "default_model": [],
                "default_effort": None,
                "default_harnesses": ["claude_code", "codex"],
                "intended_migration": None,
            },
        )

    bound = bind_stubs(patch=fake_patch)
    result = bound.runner.invoke(
        hub_group,
        ["chunk", "set", "ch_1", "--default-harnesses", "claude_code", "--default-harnesses", "codex"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        ("http://hub.local:8421/api/chunks/ch_1", {"default_harnesses": ["claude_code", "codex"]}),
    ]
    assert "default harnesses → claude_code, codex" in result.output


@pytest.mark.unit
def test_show_reads_both_defaults_back_in_text_mode() -> None:
    """`chunk set` can write both, so text mode has to read both back — otherwise an
    operator can only see what they wrote via `--json`."""

    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            {
                "chunk_id": "ch_1",
                "status": "not_ready",
                "graph_id": "gr_1",
                "current_node_name": "build",
                "default_model": ["blizzard:advanced", "blizzard:basic"],
                "default_effort": "high",
                "cost": {},
            },
        )

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["chunk", "get", "ch_1"])

    assert result.exit_code == 0, result.output
    assert "default model: blizzard:advanced, blizzard:basic" in result.output
    assert "default effort: high" in result.output


@pytest.mark.unit
def test_show_dashes_a_chunk_expressing_no_preference() -> None:
    """No preference set reads as a dash rather than as unknown or as a fabricated
    model name."""

    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            {
                "chunk_id": "ch_1",
                "status": "not_ready",
                "graph_id": "gr_1",
                "current_node_name": "build",
                "default_model": [],
                "default_effort": None,
                "cost": {},
            },
        )

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["chunk", "get", "ch_1"])

    assert result.exit_code == 0, result.output
    assert "default model: -   default effort: -" in result.output


@pytest.mark.unit
def test_show_reads_default_harnesses_back_in_text_mode() -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            {
                "chunk_id": "ch_1",
                "status": "not_ready",
                "graph_id": "gr_1",
                "current_node_name": "build",
                "default_model": [],
                "default_effort": None,
                "default_harnesses": ["claude_code", "codex"],
                "cost": {},
            },
        )

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["chunk", "get", "ch_1"])

    assert result.exit_code == 0, result.output
    assert "default harnesses: claude_code, codex" in result.output


# `chunk get` — blocked marking and neighborhood.
# --------------------------------------------------------------------------- #


def _detail(chunk_id: str, **extra: object) -> _FakeResponse:
    return _FakeResponse(
        200,
        {
            "chunk_id": chunk_id,
            "status": "not_ready",
            "graph_id": "gr_1",
            "current_node_name": "build",
            "default_model": [],
            "default_effort": None,
            "cost": {},
            **extra,
        },
    )


@pytest.mark.unit
def test_show_renders_blocked_naming_the_prerequisite() -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _detail("ch_1", blocked={"prerequisite_chunk_id": "ch_prereq"})

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["chunk", "get", "ch_1"])

    assert result.exit_code == 0, result.output
    assert "blocked: waiting on ch_prereq" in result.output


@pytest.mark.unit
def test_show_renders_neighborhood_marking_satisfied_and_unsatisfied() -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _detail(
            "ch_1",
            neighborhood={
                "prerequisites": [
                    {"chunk_id": "ch_a", "status": "done", "satisfied": True},
                    {"chunk_id": "ch_b", "status": "running", "satisfied": False},
                ],
                "dependents": [{"chunk_id": "ch_c", "status": "not_ready", "satisfied": False}],
            },
        )

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["chunk", "get", "ch_1"])

    assert result.exit_code == 0, result.output
    assert "prerequisites: ch_a (satisfied), ch_b (unsatisfied)" in result.output
    assert "dependents: ch_c (unsatisfied)" in result.output


@pytest.mark.unit
def test_show_gains_no_lines_when_no_blocked_marking_or_edges() -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _detail("ch_1", blocked=None, neighborhood={"prerequisites": [], "dependents": []})

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["chunk", "get", "ch_1"])

    assert result.exit_code == 0, result.output
    assert "blocked" not in result.output
    assert "prerequisites" not in result.output
    assert "dependents" not in result.output


@pytest.mark.unit
@pytest.mark.parametrize(("status", "shown"), [("running", True), ("done", False), ("stopped", False)])
def test_show_prints_the_runner_only_while_the_chunk_holds_its_claim(status: str, shown: bool) -> None:
    route = {"runner_id": "rn_1", "environment_ids": ["env_1"]}
    bound = bind_stubs(get=lambda *a, **k: _detail("ch_1", status=status, route=route))
    result = bound.runner.invoke(hub_group, ["chunk", "get", "ch_1"])

    assert result.exit_code == 0, result.output
    assert ("runner: rn_1  environments: 1" in result.output) is shown


@pytest.mark.unit
def test_show_folds_an_estimate_into_the_cost_line_with_a_tilde() -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _detail("ch_1", cost={"cost_usd": 0.0, "cost_partial": False, "estimated_cost_usd": 0.03})

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["chunk", "get", "ch_1"])

    assert result.exit_code == 0, result.output
    assert "cost: ~$0.03" in result.output


@pytest.mark.unit
def test_show_marks_no_estimate_when_none_was_reported() -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _detail("ch_1", cost={"cost_usd": 0.10, "cost_partial": False})

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["chunk", "get", "ch_1"])

    assert result.exit_code == 0, result.output
    assert "cost: $0.10" in result.output
    assert "~$" not in result.output


# `chunk depend` / `chunk release-dependency` — pure clients of the two
# existing dependency routes.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_depend_declares_and_defaults_by_to_operator() -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(
            202,
            {
                "dependency_id": "dep_1",
                "dependent_chunk_id": "ch_1",
                "prerequisite_chunk_id": "ch_2",
                "declared_at": "2026-01-01T00:00:00+00:00",
                "declared_by": "operator",
            },
        )

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        hub_group, ["chunk", "depend", "ch_1", "ch_2"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "http://hub.local:8421/api/chunks/ch_1/dependencies",
            {"prerequisite_chunk_id": "ch_2", "by": "operator"},
        )
    ]
    assert "ch_1" in result.output
    assert "ch_2" in result.output


@pytest.mark.unit
def test_depend_sends_a_named_by() -> None:
    calls: list[object] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse(
            202,
            {
                "dependency_id": "dep_1",
                "dependent_chunk_id": "ch_1",
                "prerequisite_chunk_id": "ch_2",
                "declared_at": "2026-01-01T00:00:00+00:00",
                "declared_by": "ada",
            },
        )

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "depend", "ch_1", "ch_2", "--by", "ada"])

    assert result.exit_code == 0, result.output
    assert calls == [{"prerequisite_chunk_id": "ch_2", "by": "ada"}]


@pytest.mark.unit
def test_depend_refuses_a_dependent_not_editable() -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"chunk_id": "ch_1", "status": "running", "detail": "not editable"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "depend", "ch_1", "ch_2"])

    assert result.exit_code != 0
    assert "ch_1" in result.output
    assert "running" in result.output


@pytest.mark.unit
def test_depend_refuses_a_cycle_naming_the_pair() -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            409,
            {
                "dependent_chunk_id": "ch_1",
                "prerequisite_chunk_id": "ch_2",
                "detail": "would close a cycle",
            },
        )

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "depend", "ch_1", "ch_2"])

    assert result.exit_code != 0
    assert "ch_1" in result.output
    assert "ch_2" in result.output
    assert "cycle" in result.output


@pytest.mark.unit
def test_depend_refuses_an_ephemeral_prerequisite() -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"chunk_id": "ch_2", "detail": "ephemeral"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "depend", "ch_1", "ch_2"])

    assert result.exit_code != 0
    assert "ch_2" in result.output
    assert "ephemeral" in result.output


@pytest.mark.unit
def test_depend_maps_404_naming_both_chunks() -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "depend", "ch_1", "ch_2"])

    assert result.exit_code != 0
    assert "ch_1" in result.output
    assert "ch_2" in result.output


@pytest.mark.unit
def test_release_dependency_releases_and_defaults_by_to_operator() -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(
            202,
            {
                "dependency_id": "dep_1",
                "dependent_chunk_id": "ch_1",
                "prerequisite_chunk_id": "ch_2",
                "declared_at": "2026-01-01T00:00:00+00:00",
                "declared_by": "operator",
                "released_at": "2026-01-02T00:00:00+00:00",
                "released_by": "operator",
            },
        )

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        hub_group,
        ["chunk", "release-dependency", "ch_1", "ch_2"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "http://hub.local:8421/api/chunks/ch_1/dependencies/release",
            {"prerequisite_chunk_id": "ch_2", "by": "operator"},
        )
    ]
    assert "ch_1" in result.output
    assert "ch_2" in result.output


@pytest.mark.unit
def test_release_dependency_refuses_when_no_edge_stands() -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            409,
            {
                "dependent_chunk_id": "ch_1",
                "prerequisite_chunk_id": "ch_2",
                "detail": "no standing dependency to release",
            },
        )

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "release-dependency", "ch_1", "ch_2"])

    assert result.exit_code != 0
    assert "ch_1" in result.output
    assert "ch_2" in result.output


@pytest.mark.unit
def test_release_dependency_maps_404() -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "release-dependency", "ch_1", "ch_2"])

    assert result.exit_code != 0
    assert "ch_1" in result.output


@pytest.mark.unit
def test_chunk_list_marks_a_blocked_chunk() -> None:
    def fake_get(url: str, *, timeout: float, params: object | None = None) -> _FakeResponse:
        return _FakeResponse(
            200,
            {
                "chunks": [
                    {
                        "chunk_id": "ch_1",
                        "status": "not_ready",
                        "current_node_id": "nd_1",
                        "cost": {},
                        "blocked": {"prerequisite_chunk_id": "ch_prereq"},
                    },
                    {"chunk_id": "ch_2", "status": "ready", "current_node_id": "nd_1", "cost": {}},
                ],
                "next_cursor": None,
            },
        )

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["chunk", "list"])

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert "[blocked on ch_prereq]" in lines[0]
    assert "[blocked" not in lines[1]


@pytest.mark.unit
def test_chunk_list_folds_an_estimate_into_the_one_cost_figure() -> None:
    def fake_get(url: str, *, timeout: float, params: object | None = None) -> _FakeResponse:
        return _FakeResponse(
            200,
            {
                "chunks": [
                    {
                        "chunk_id": "ch_1",
                        "status": "running",
                        "current_node_id": "nd_1",
                        "cost": {"cost_usd": 0.0, "cost_partial": False, "estimated_cost_usd": 0.12},
                        "blocked": {"prerequisite_chunk_id": "ch_prereq"},
                    },
                    {"chunk_id": "ch_2", "status": "ready", "current_node_id": "nd_1", "cost": {"cost_usd": 0.5}},
                ],
                "next_cursor": None,
            },
        )

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["chunk", "list"])

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].endswith("~$0.12  [blocked on ch_prereq]")
    assert lines[1].endswith("$0.50")


@pytest.mark.unit
def test_chunk_list_drains_every_page() -> None:
    page_1 = {
        "chunks": [
            {"chunk_id": "ch_1", "status": "ready", "current_node_id": "nd_1", "cost": {}},
            {"chunk_id": "ch_2", "status": "ready", "current_node_id": "nd_1", "cost": {}},
        ],
        "next_cursor": "cursor-1",
    }
    page_2 = {
        "chunks": [
            {"chunk_id": "ch_3", "status": "ready", "current_node_id": "nd_1", "cost": {}},
        ],
        "next_cursor": None,
    }

    def fake_get(url: str, *, timeout: float, params: object | None = None) -> _FakeResponse:
        if isinstance(params, dict) and params.get("cursor") is not None:
            return _FakeResponse(200, page_2)
        return _FakeResponse(200, page_1)

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["chunk", "list"])

    assert result.exit_code == 0, result.output
    assert "ch_1" in result.output
    assert "ch_2" in result.output
    assert "ch_3" in result.output


@pytest.mark.unit
@pytest.mark.parametrize("verb", ["promote", "demote"])
def test_placement_verbs_post_the_anchor_when_named(verb: str) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object = None, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"chunk_id": "ch_1", "status": "not_ready"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        hub_group, ["chunk", verb, "ch_1", "--after", "ch_2"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    assert calls == [(f"http://hub.local:8421/api/chunks/ch_1/{verb}", {"after_chunk_id": "ch_2"})]
