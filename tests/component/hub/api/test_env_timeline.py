"""``GET /api/env-timeline``, ``/api/env-timeline/step`` and ``/api/env-timeline/step/transcript`` over a fully wired
hub (component tier)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.domain.observability.transcripts import TranscriptSlice
from blizzard.hub.store import schema as s
from tests.support.scaffolding import (
    HubHarness,
    SeedingTranscriptSegmentStore,
    build_hub,
    count_queries,
    hub_store_connections,
    mint_graph,
    only_tenant_subquery,
    seed_chunk,
    seed_project,
    stamp,
)

_WAY_BACK = "2020-01-01T00:00:00Z"


def _insert(hub: HubHarness, table: sa.Table, **values: Any) -> None:
    with hub.engine.begin() as conn:
        conn.execute(sa.insert(table).values(stamp(table, **values)))


def _routed_hub(tmp_path: Path, *, envs: tuple[str, ...] = ("env_a",)) -> HubHarness:
    """A chunk routed onto ``envs`` of runner ``rn_1`` (on host ``xps``) that built for five minutes, moved to review
    and has sat there five minutes since; the clock reads ten minutes after the route."""
    hub = build_hub(tmp_path)
    t0 = hub.clock.now()
    mint_graph(hub.engine, "gr_1", name="flow", nodes={"nd_build": "build", "nd_review": "review"}, at=t0)
    with hub.engine.begin() as conn:
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=t0)
    _insert(hub, s.hosts, host_id="hs_1", name="xps", added_at=t0)
    _insert(hub, s.runner_registrations, runner_id="rn_1", name="alpha", added_at=t0, host_id="hs_1")
    _insert(
        hub,
        s.route_created,
        route_id="rt_1",
        chunk_id="ch_1",
        runner_id="rn_1",
        workspace_id="w1",
        created_at=t0,
        seq=1,
    )
    for env in envs:
        _insert(hub, s.route_environments, route_id="rt_1", environment_id=env)
    _insert(hub, s.lease_facts, chunk_id="ch_1", epoch=1, runner_id="rn_1", minted_at=t0, lease_id="ls_1")
    _insert(
        hub,
        s.transitions,
        transition_id="tr_1",
        chunk_id="ch_1",
        graph_id="gr_1",
        from_node_id="nd_build",
        to_node_id="nd_review",
        choice_name="ok",
        epoch=1,
        runner_id="rn_1",
        recorded_at=t0 + timedelta(minutes=5),
    )
    _insert(
        hub,
        s.lease_facts,
        chunk_id="ch_1",
        epoch=2,
        runner_id="rn_1",
        minted_at=t0 + timedelta(minutes=5),
        lease_id="ls_2",
    )
    hub.clock.advance(timedelta(minutes=10))
    return hub


def _since(hub: HubHarness) -> str:
    return iso_utc(hub.clock.now() - timedelta(hours=1))


def test_the_timeline_lays_a_routed_chunk_on_host_workspace_and_environment(tmp_path: Path) -> None:
    hub = _routed_hub(tmp_path)

    resp = hub.client.get("/api/env-timeline", params={"since": _since(hub)})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    (host,) = body["hosts"]
    assert (host["host_id"], host["host_name"], host["runner_names"]) == ("hs_1", "xps", ["alpha"])
    (workspace,) = host["workspaces"]
    assert workspace["workspace_id"] == "w1"
    (env,) = workspace["environments"]
    assert env["environment_id"] == "env_a"
    assert [(b["node_id"], b["exit_choice"], b["lease_id"]) for b in env["blocks"]] == [
        ("nd_build", "ok", "ls_1"),
        ("nd_review", None, "ls_2"),
    ]
    assert body["runners"] == {"rn_1": "alpha"}
    assert "ch_1" in body["chunks"]
    assert body["nodes"]["nd_review"] == {"graph_name": "flow", "node_name": "review"}


def test_a_missing_since_is_unprocessable(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    assert hub.client.get("/api/env-timeline").status_code == 422


def test_a_malformed_since_is_unprocessable(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    assert hub.client.get("/api/env-timeline", params={"since": "yesterday"}).status_code == 422


def test_latest_answers_one_block_per_environment(tmp_path: Path) -> None:
    hub = _routed_hub(tmp_path, envs=("env_a", "env_b"))

    resp = hub.client.get("/api/env-timeline", params={"since": _since(hub), "latest": "true"})

    assert resp.status_code == 200, resp.text
    envs = resp.json()["hosts"][0]["workspaces"][0]["environments"]
    assert {e["environment_id"]: [b["node_id"] for b in e["blocks"]] for e in envs} == {
        "env_a": ["nd_review"],
        "env_b": ["nd_review"],
    }


def test_a_window_wider_than_fourteen_days_is_narrowed_to_the_fourteen_before_until(tmp_path: Path) -> None:
    hub = _routed_hub(tmp_path)
    until = hub.clock.now()

    resp = hub.client.get("/api/env-timeline", params={"since": _WAY_BACK, "until": iso_utc(until)})

    assert resp.status_code == 200, resp.text
    assert resp.json()["since"] == iso_utc(until - timedelta(days=14))


def test_the_step_is_found_by_lease_id(tmp_path: Path) -> None:
    hub = _routed_hub(tmp_path)

    resp = hub.client.get("/api/env-timeline/step", params={"chunk_id": "ch_1", "lease_id": "ls_1"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["step"]["segment"]["node_id"] == "nd_build"
    assert body["step"]["environment_id"] == "env_a"
    assert body["step"]["host_name"] == "xps"
    assert [p["segment"]["node_id"] for p in body["journey"]] == ["nd_build", "nd_review"]


def test_the_step_is_found_by_started_at(tmp_path: Path) -> None:
    hub = _routed_hub(tmp_path)
    started = hub.clock.now() - timedelta(minutes=5)

    resp = hub.client.get("/api/env-timeline/step", params={"chunk_id": "ch_1", "started_at": iso_utc(started)})

    assert resp.status_code == 200, resp.text
    assert resp.json()["step"]["segment"]["node_id"] == "nd_review"


def test_the_step_defaults_to_the_chunks_newest(tmp_path: Path) -> None:
    hub = _routed_hub(tmp_path)

    resp = hub.client.get("/api/env-timeline/step", params={"chunk_id": "ch_1"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["step"]["segment"]["node_id"] == "nd_review"


def _problem(resp: Any, kind: str) -> dict[str, Any]:
    assert resp.status_code == 404, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert (body["type"], body["status"]) == (f"urn:blizzard:problem:{kind}", 404)
    return body


def test_the_step_of_an_unknown_chunk_is_not_found(tmp_path: Path) -> None:
    hub = _routed_hub(tmp_path)

    body = _problem(hub.client.get("/api/env-timeline/step", params={"chunk_id": "ch_nope"}), "chunk-not-found")

    assert body["detail"] == "unknown chunk ch_nope"


def test_the_step_of_an_unknown_lease_is_not_found(tmp_path: Path) -> None:
    hub = _routed_hub(tmp_path)

    resp = hub.client.get("/api/env-timeline/step", params={"chunk_id": "ch_1", "lease_id": "ls_nope"})

    assert _problem(resp, "node-step-not-found")["detail"] == "no such node step of ch_1"


def test_a_move_across_graphs_recorded_without_its_landing_lands_on_the_target_entry(tmp_path: Path) -> None:
    hub = _routed_hub(tmp_path)
    t0 = hub.clock.now() - timedelta(minutes=10)
    mint_graph(hub.engine, "gr_2", name="next", nodes={"nd_plan": "plan"}, at=t0)
    _insert(
        hub,
        s.chunk_migrations,
        migration_id="mg_1",
        chunk_id="ch_1",
        from_node_id="nd_review",
        from_graph_id="gr_1",
        to_graph_id="gr_2",
        landed_node_id=None,
        choice_name="move",
        epoch=2,
        recorded_at=t0 + timedelta(minutes=8),
    )

    timeline = hub.client.get("/api/env-timeline", params={"since": _since(hub)})
    step = hub.client.get("/api/env-timeline/step", params={"chunk_id": "ch_1"})

    assert timeline.status_code == 200, timeline.text
    (env,) = timeline.json()["hosts"][0]["workspaces"][0]["environments"]
    assert [(b["node_id"], b["exit_choice"]) for b in env["blocks"]] == [
        ("nd_build", "ok"),
        ("nd_review", "move"),
        ("nd_plan", None),
    ]
    assert step.status_code == 200, step.text
    assert (step.json()["step"]["segment"]["graph_id"], step.json()["step"]["segment"]["node_name"]) == (
        "gr_2",
        "plan",
    )


def test_a_malformed_started_at_is_unprocessable(tmp_path: Path) -> None:
    hub = _routed_hub(tmp_path)

    resp = hub.client.get("/api/env-timeline/step", params={"chunk_id": "ch_1", "started_at": "soon"})

    assert resp.status_code == 422


def _block(hub: HubHarness, **params: str) -> list[dict[str, Any]]:
    body = hub.client.get("/api/env-timeline", params={"since": _since(hub), **params}).json()
    return [b for h in body["hosts"] for ws in h["workspaces"] for env in ws["environments"] for b in env["blocks"]]


def test_each_block_says_where_its_node_runs_and_whether_its_escalation_is_still_open(tmp_path: Path) -> None:
    hub = _routed_hub(tmp_path)
    t0 = hub.clock.now() - timedelta(minutes=10)
    _insert(hub, s.escalations, chunk_id="ch_1", epoch=2, cause="stuck", recorded_at=t0 + timedelta(minutes=7))

    blocks = _block(hub)

    assert [(b["node_id"], b["executor"], b["escalation"], b["escalation_open"]) for b in blocks] == [
        ("nd_build", "runner", None, False),
        ("nd_review", "runner", "stuck", True),
    ]

    _insert(hub, s.requeues, chunk_id="ch_1", requeued_at=t0 + timedelta(minutes=8))

    review = _block(hub)[-1]
    assert (review["failed"], review["escalation"], review["escalation_open"]) == (True, "stuck", False)


def test_the_step_carries_its_spend_and_the_keys_of_its_own_artifacts(tmp_path: Path) -> None:
    hub = _routed_hub(tmp_path)
    t0 = hub.clock.now() - timedelta(minutes=10)
    for i, (cost, estimate) in enumerate(((1.0, None), (None, 0.25))):
        _insert(
            hub,
            s.usage_facts,
            chunk_id="ch_1",
            node_id="nd_build",
            epoch=1,
            runner_id="rn_1",
            kind="spawn",
            model="opus",
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=0,
            cache_create_tokens=0,
            cost_usd=cost,
            estimated_cost_usd=estimate,
            recorded_at=t0 + timedelta(minutes=1 + i),
        )
    for epoch, name in ((1, "plan"), (2, "later")):
        _insert(
            hub,
            s.artifacts,
            artifact_id=Id.mint_at(IdPrefix.ARTIFACT, t0 + timedelta(minutes=2)).value,
            chunk_id="ch_1",
            node_id="nd_build",
            node_name="build",
            epoch=epoch,
            name=name,
            kind="asset",
            data="x",
            produced_at=t0,
            seq=epoch,
        )

    body = hub.client.get("/api/env-timeline/step", params={"chunk_id": "ch_1", "lease_id": "ls_1"}).json()

    assert body["spend"] == {
        "input_tokens": 20,
        "output_tokens": 10,
        "cache_read_tokens": 0,
        "cache_create_tokens": 0,
        "cost_usd": 1.0,
        "cost_partial": False,
        "estimated_cost_usd": 0.25,
        "billed_partial": True,
    }
    assert body["artifact_keys"] == ["build.plan.1"]
    newest = hub.client.get("/api/env-timeline/step", params={"chunk_id": "ch_1"}).json()
    assert (newest["spend"], newest["artifact_keys"]) == (None, [])


def test_under_project_only_that_projects_chunks_are_laid_on_the_environments(tmp_path: Path) -> None:
    hub = _routed_hub(tmp_path)
    t0 = hub.clock.now() - timedelta(minutes=10)
    other = seed_project(hub.engine, "other")
    with hub.engine.begin() as conn:
        conn.execute(
            sa.insert(s.chunks).values(
                chunk_id="ch_2", graph_id="gr_1", minted_at=t0, tenant_id=only_tenant_subquery(), project_id=other
            )
        )
    _insert(
        hub,
        s.route_created,
        route_id="rt_2",
        chunk_id="ch_2",
        runner_id="rn_1",
        workspace_id="w1",
        created_at=t0,
        seq=1,
    )
    _insert(hub, s.route_environments, route_id="rt_2", environment_id="env_b")

    assert {b["chunk_id"] for b in _block(hub)} == {"ch_1", "ch_2"}
    assert {b["chunk_id"] for b in _block(hub, project=other)} == {"ch_2"}
    assert {b["chunk_id"] for b in _block(hub, project=other, latest="true")} == {"ch_2"}


def _fleet(tmp_path: Path, chunks: int) -> HubHarness:
    """``chunks`` chunks, each routed onto an environment of its own, that built and moved on to review."""
    tmp_path.mkdir()
    hub = build_hub(tmp_path)
    t0 = hub.clock.now()
    mint_graph(hub.engine, "gr_1", name="flow", nodes={"nd_build": "build", "nd_review": "review"}, at=t0)
    _insert(hub, s.runner_registrations, runner_id="rn_1", name="alpha", added_at=t0)
    for i in range(chunks):
        chunk_id = f"ch_{i}"
        with hub.engine.begin() as conn:
            seed_chunk(conn, chunk_id, graph_id="gr_1", at=t0)
        _insert(
            hub,
            s.route_created,
            route_id=f"rt_{i}",
            chunk_id=chunk_id,
            runner_id="rn_1",
            workspace_id="w1",
            created_at=t0,
            seq=1,
        )
        _insert(hub, s.route_environments, route_id=f"rt_{i}", environment_id=f"env_{i}")
        _insert(hub, s.lease_facts, chunk_id=chunk_id, epoch=1, runner_id="rn_1", minted_at=t0, lease_id=f"ls_{i}")
        _insert(
            hub,
            s.transitions,
            transition_id=f"tr_{i}",
            chunk_id=chunk_id,
            graph_id="gr_1",
            from_node_id="nd_build",
            to_node_id="nd_review",
            choice_name="ok",
            epoch=1,
            runner_id="rn_1",
            recorded_at=t0 + timedelta(minutes=5),
        )
    hub.clock.advance(timedelta(minutes=10))
    return hub


def test_the_timeline_and_step_reads_issue_as_many_statements_for_a_large_fleet_as_a_small_one(
    tmp_path: Path,
) -> None:
    small, large = _fleet(tmp_path / "small", 1), _fleet(tmp_path / "large", 12)
    paths: list[tuple[str, dict[str, str]]] = [
        ("/api/env-timeline", {}),
        ("/api/env-timeline", {"latest": "true"}),
        ("/api/env-timeline/step", {"chunk_id": "ch_0"}),
    ]

    def read(hub: HubHarness, path: str, params: dict[str, str]) -> int:
        return hub.client.get(path, params={"since": _since(hub), **params}).status_code

    for path, params in paths:
        assert read(small, path, params) == 200
        assert count_queries(small.engine, lambda p=path, q=params: read(small, p, q)) == count_queries(
            large.engine, lambda p=path, q=params: read(large, p, q)
        ), path


def _shipped(hub: HubHarness, segment_id: str, *, generation: int, turns: str, **extra: Any) -> None:
    store = SeedingTranscriptSegmentStore(hub_store_connections(hub.engine))
    record = TranscriptSlice(
        segment_id=segment_id,
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        spawn_generation=generation,
        runner_id="rn_1",
        turn_range_start=0,
        turn_range_end=0,
        final=True,
        normalizer_version="v1",
        harness_version="claude-code-1.0",
        record_truncated=extra.pop("record_truncated", False),
        turns_json=turns,
        **extra,
    )
    store.insert_accepted(record, byte_count=len(turns), at=hub.clock.now())


def _turn(text: str) -> str:
    turn = {
        "index": 0,
        "kind": "asst",
        "timestamp": None,
        "text": text,
        "tool": None,
        "thinking_redacted": False,
        "sidechain": None,
        "truncated": False,
    }
    return json.dumps([turn])


def _transcript(hub: HubHarness, **params: str | int) -> Any:
    return hub.client.get(
        "/api/env-timeline/step/transcript", params={"chunk_id": "ch_1", "node_id": "nd_build", "epoch": 1, **params}
    )


def test_the_steps_transcript_joins_every_generation_and_leaves_out_a_replaced_shipment(tmp_path: Path) -> None:
    hub = _routed_hub(tmp_path)
    _shipped(hub, "sg_old", generation=1, turns=_turn("first copy"), record_truncated=True)
    _shipped(hub, "sg_new", generation=1, turns=_turn("second copy"), supersedes="sg_old")
    _shipped(hub, "sg_resume", generation=2, turns=_turn("resumed"))

    resp = _transcript(hub)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["chunk_id"], body["node_id"], body["epoch"], body["truncated"]) == ("ch_1", "nd_build", 1, False)
    assert [(t["index"], t["text"]) for t in body["turns"]] == [(0, "second copy"), (1, "resumed")]


def test_a_step_whose_session_shipped_nothing_has_an_empty_transcript(tmp_path: Path) -> None:
    hub = _routed_hub(tmp_path)

    body = _transcript(hub, epoch=2).json()

    assert (body["truncated"], body["turns"]) == (False, [])


def test_the_transcript_of_an_unknown_chunk_is_not_found(tmp_path: Path) -> None:
    hub = _routed_hub(tmp_path)

    body = _problem(_transcript(hub, chunk_id="ch_nope"), "chunk-not-found")

    assert (body["detail"], body["instance"]) == ("unknown chunk ch_nope", "/api/env-timeline/step/transcript")
