"""``blizzard hub chunk migrate`` / ``restart`` / ``set`` / ``show`` (unit tier) — pure
clients of ``PATCH``/``GET /api/chunks/{id}``, driven here with ``httpx`` stubbed
(issues #124, #144).
"""

from __future__ import annotations

import json

import httpx
import pytest
from click.testing import CliRunner

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
            "default_model": ["blizzard:basic"],
            "default_effort": "medium",
            "intended_migration": intended_migration,
        },
    )


@pytest.mark.unit
def test_migrate_forced_sends_to_graph_and_node(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _patch_response("ch_1", {"mode": "forced", "graph_id": "gr_2", "graph_name": "beta", "node_name": "n2"})

    monkeypatch.setattr(httpx, "patch", fake_patch)
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

    monkeypatch.setattr(httpx, "patch", fake_patch)
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

    monkeypatch.setattr(httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "migrate", "ch_1", "--cancel"])

    assert result.exit_code == 0, result.output
    assert calls == [("http://127.0.0.1:8421/api/chunks/ch_1", {"intended_migration": None})]
    assert "cleared" in result.output


@pytest.mark.unit
def test_migrate_cancel_conflicts_with_to_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        raise AssertionError("must not call the API when the flags conflict")

    monkeypatch.setattr(httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "migrate", "ch_1", "--cancel", "--to-graph", "beta"])

    assert result.exit_code != 0
    assert "--cancel" in result.output


@pytest.mark.unit
def test_migrate_cancel_conflicts_with_node(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        raise AssertionError("must not call the API when the flags conflict")

    monkeypatch.setattr(httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "migrate", "ch_1", "--cancel", "--node", "n2"])

    assert result.exit_code != 0
    assert "--cancel" in result.output


@pytest.mark.unit
def test_migrate_without_to_graph_or_cancel_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        raise AssertionError("must not call the API without --to-graph or --cancel")

    monkeypatch.setattr(httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "migrate", "ch_1"])

    assert result.exit_code != 0
    assert "--to-graph" in result.output


@pytest.mark.unit
def test_migrate_maps_409_to_the_server_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "chunk is already pinned to graph gr_2"})

    monkeypatch.setattr(httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "migrate", "ch_1", "--to-graph", "beta"])

    assert result.exit_code != 0
    assert "already pinned to graph gr_2" in result.output


@pytest.mark.unit
def test_migrate_maps_422_to_the_server_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(422, {"detail": "node must not be blank"})

    monkeypatch.setattr(httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "migrate", "ch_1", "--to-graph", "beta", "--node", " "])

    assert result.exit_code != 0
    assert "node must not be blank" in result.output


@pytest.mark.unit
def test_migrate_maps_404_to_the_server_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {"detail": "unknown chunk ch_ghost"})

    monkeypatch.setattr(httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "migrate", "ch_ghost", "--to-graph", "beta"])

    assert result.exit_code != 0
    assert "unknown chunk ch_ghost" in result.output


@pytest.mark.unit
def test_migrate_json_prints_the_raw_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "default_model": ["blizzard:basic"],
        "default_effort": "medium",
        "intended_migration": {"mode": "auto", "graph_id": "gr_2", "graph_name": "beta", "node_name": None},
    }

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(202, payload)

    monkeypatch.setattr(httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "migrate", "ch_1", "--to-graph", "beta", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload


# `chunk restart` (#370, #371) — an event, not the standing intent `migrate` records.
# --------------------------------------------------------------------------- #


def _summary(chunk_id: str, node_name: str) -> _FakeResponse:
    return _FakeResponse(202, {"chunk_id": chunk_id, "status": "running", "current_node_name": node_name})


@pytest.mark.unit
def test_restart_without_a_node_posts_a_null_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """The common case — restart this step on clean context; the hub resolves the node."""
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _summary("ch_1", "build")

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["chunk", "restart", "ch_1"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    posted = ("http://hub.local:8421/api/chunks/ch_1/restart", {"node": None, "to_graph": None, "by": "operator"})
    assert calls == [posted]
    assert "build" in result.output


@pytest.mark.unit
def test_restart_sends_the_named_node_and_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _summary("ch_1", "plan")

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["chunk", "restart", "ch_1", "--node", "plan", "--by", "ada"])

    assert result.exit_code == 0, result.output
    assert calls[0][1] == {"node": "plan", "to_graph": None, "by": "ada"}


@pytest.mark.unit
def test_restart_sends_the_cross_graph_target_and_names_it_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--to-graph` is the eager move `migrate` only ever schedules (#371)."""
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _summary("ch_1", "build")

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["chunk", "restart", "ch_1", "--to-graph", "beta"])

    assert result.exit_code == 0, result.output
    assert calls[0][1] == {"node": None, "to_graph": "beta", "by": "operator"}
    assert "beta" in result.output


@pytest.mark.unit
def test_restart_maps_409_to_the_server_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "node 'deploy' does not exist on graph gr_1"})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["chunk", "restart", "ch_1", "--node", "deploy"])

    assert result.exit_code != 0
    assert "does not exist on graph gr_1" in result.output


# `chunk set --default-model/--default-effort` (issue #144) — the CLI-only surface.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_set_sends_repeated_default_model_flags_as_an_ordered_list(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(httpx, "patch", fake_patch)
    result = CliRunner().invoke(
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
def test_set_omits_a_field_the_operator_did_not_name(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["chunk", "set", "ch_1", "--default-effort", "high"])

    assert result.exit_code == 0, result.output
    assert calls == [{"default_effort": "high"}]


@pytest.mark.unit
def test_set_with_no_option_at_all_is_a_usage_error() -> None:
    result = CliRunner().invoke(hub_group, ["chunk", "set", "ch_1"])

    assert result.exit_code != 0
    assert "--default-model" in result.output


@pytest.mark.unit
def test_show_reads_both_defaults_back_in_text_mode(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["chunk", "show", "ch_1"])

    assert result.exit_code == 0, result.output
    assert "default model: blizzard:advanced, blizzard:basic" in result.output
    assert "default effort: high" in result.output


@pytest.mark.unit
def test_show_dashes_a_chunk_expressing_no_preference(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["chunk", "show", "ch_1"])

    assert result.exit_code == 0, result.output
    assert "default model: -   default effort: -" in result.output


# `chunk show` — blocked marking and neighborhood (issue #476/#457/#462).
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
def test_show_renders_blocked_naming_the_prerequisite(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _detail("ch_1", blocked={"prerequisite_chunk_id": "ch_prereq"})

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["chunk", "show", "ch_1"])

    assert result.exit_code == 0, result.output
    assert "blocked: waiting on ch_prereq" in result.output


@pytest.mark.unit
def test_show_renders_neighborhood_marking_satisfied_and_unsatisfied(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["chunk", "show", "ch_1"])

    assert result.exit_code == 0, result.output
    assert "prerequisites: ch_a (satisfied), ch_b (unsatisfied)" in result.output
    assert "dependents: ch_c (unsatisfied)" in result.output


@pytest.mark.unit
def test_show_gains_no_lines_when_no_blocked_marking_or_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _detail("ch_1", blocked=None, neighborhood={"prerequisites": [], "dependents": []})

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["chunk", "show", "ch_1"])

    assert result.exit_code == 0, result.output
    assert "blocked" not in result.output
    assert "prerequisites" not in result.output
    assert "dependents" not in result.output


# `chunk depend` / `chunk release-dependency` (issue #476) — pure clients of the two
# existing dependency routes.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_depend_declares_and_defaults_by_to_operator(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
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
def test_depend_sends_a_named_by(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["chunk", "depend", "ch_1", "ch_2", "--by", "ada"])

    assert result.exit_code == 0, result.output
    assert calls == [{"prerequisite_chunk_id": "ch_2", "by": "ada"}]


@pytest.mark.unit
def test_depend_refuses_a_dependent_not_editable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"chunk_id": "ch_1", "status": "running", "detail": "not editable"})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["chunk", "depend", "ch_1", "ch_2"])

    assert result.exit_code != 0
    assert "ch_1" in result.output
    assert "running" in result.output


@pytest.mark.unit
def test_depend_refuses_a_cycle_naming_the_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            409,
            {
                "dependent_chunk_id": "ch_1",
                "prerequisite_chunk_id": "ch_2",
                "detail": "would close a cycle",
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["chunk", "depend", "ch_1", "ch_2"])

    assert result.exit_code != 0
    assert "ch_1" in result.output
    assert "ch_2" in result.output
    assert "cycle" in result.output


@pytest.mark.unit
def test_depend_refuses_an_ephemeral_prerequisite(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"chunk_id": "ch_2", "detail": "ephemeral"})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["chunk", "depend", "ch_1", "ch_2"])

    assert result.exit_code != 0
    assert "ch_2" in result.output
    assert "ephemeral" in result.output


@pytest.mark.unit
def test_depend_maps_404_naming_both_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["chunk", "depend", "ch_1", "ch_2"])

    assert result.exit_code != 0
    assert "ch_1" in result.output
    assert "ch_2" in result.output


@pytest.mark.unit
def test_release_dependency_releases_and_defaults_by_to_operator(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
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
def test_release_dependency_refuses_when_no_edge_stands(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            409,
            {
                "dependent_chunk_id": "ch_1",
                "prerequisite_chunk_id": "ch_2",
                "detail": "no standing dependency to release",
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["chunk", "release-dependency", "ch_1", "ch_2"])

    assert result.exit_code != 0
    assert "ch_1" in result.output
    assert "ch_2" in result.output


@pytest.mark.unit
def test_release_dependency_maps_404(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["chunk", "release-dependency", "ch_1", "ch_2"])

    assert result.exit_code != 0
    assert "ch_1" in result.output


@pytest.mark.unit
def test_chunk_list_marks_a_blocked_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            [
                {
                    "chunk_id": "ch_1",
                    "status": "not_ready",
                    "current_node_id": "nd_1",
                    "cost": {},
                    "blocked": {"prerequisite_chunk_id": "ch_prereq"},
                },
                {"chunk_id": "ch_2", "status": "ready", "current_node_id": "nd_1", "cost": {}},
            ],
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["chunk", "list"])

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert "[blocked on ch_prereq]" in lines[0]
    assert "[blocked" not in lines[1]
