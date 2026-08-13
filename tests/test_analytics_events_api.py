"""The analytics events/counts routes (blizzard#255, Phase 3, component tier): the
TRANSCRIPT_READ auth triad, a runner principal refused at every route, filters and
paging over real derived events, and the four counts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.auth_core import Role
from blizzard.hub.api.analytics import ScopeFilters, ndjson_lines
from blizzard.hub.config import RUNNER_AUTH_ENFORCE
from blizzard.hub.domain.analytics.extraction import EXTRACTOR_VERSION
from tests.support import build_hub, seed_session, seed_user
from tests.test_fleet_auth import _seed_enrolled

pytestmark = pytest.mark.component


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"bz_session={token}"}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _tool_turn(index: int, name: str, input: dict[str, object], *, timestamp: str | None = None) -> dict:
    return {
        "index": index,
        "kind": "tool",
        "timestamp": timestamp,
        "text": "",
        "tool": {
            "name": name,
            "input": input,
            "input_unparsed": None,
            "input_shape": "object",
            "tool_use_id": f"t{index}",
            "output": None,
            "output_truncated": False,
        },
        "thinking_redacted": False,
        "sidechain": None,
        "truncated": False,
    }


def _record(chunk_id: str, *, turns: list[dict], node_id: str = "nd_build", segment_id: str = "sg_1") -> dict:
    return {
        "seq": 1,
        "segment_id": segment_id,
        "chunk_id": chunk_id,
        "node_id": node_id,
        "epoch": 1,
        "spawn_generation": 1,
        "turn_range_start": 0,
        "turn_range_end": len(turns) - 1,
        "final": True,
        "normalizer_version": "claude-code-jsonl/2",
        "harness_version": "claude-code-1.0",
        "turns": turns,
    }


def _ingest_chunk(hub, headers: dict[str, str] | None = None, *, pointer_token: str = "default:1") -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token]}, headers=headers)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["chunk_id"])


def _seeded_hub(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A contributor session, one chunk carrying a file_read/skill_invocation/agent_spawn
    triad derived and stored, ready for the routes' own auth/filter/paging assertions."""
    hub = build_hub(tmp_path, auth_mode="oauth")
    contributor = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    token = seed_session(hub, contributor)
    chunk_id = _ingest_chunk(hub, headers=_cookie(token))
    turns = [
        _tool_turn(0, "Read", {"file_path": "src/a.py"}, timestamp="2026-08-12T09:00:00Z"),
        _tool_turn(1, "Skill", {"skill": "wf-commit"}, timestamp="2026-08-12T10:00:00Z"),
        _tool_turn(2, "Task", {"subagent_type": "explorer"}, timestamp="2026-08-12T11:00:00Z"),
    ]
    push = hub.client.post(
        "/api/fleet/transcripts", json={"runner_id": "r1", "records": [_record(chunk_id, turns=turns)]}
    )
    assert push.status_code == 200, push.text
    hub.services.event_derivation.sweep()
    return hub, token, chunk_id


# --- auth triad: 401 / 403 / 200, plus the runner-principal refusal ---------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/analytics/events",
        "/api/analytics/events/ndjson",
        "/api/analytics/counts/files",
        "/api/analytics/counts/skills",
        "/api/analytics/counts/agent-types",
        "/api/analytics/counts/nodes",
    ],
)
def test_every_route_is_401_with_no_session(tmp_path: Path, path: str) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    resp = hub.client.get(path)
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/analytics/events",
        "/api/analytics/events/ndjson",
        "/api/analytics/counts/files",
        "/api/analytics/counts/skills",
        "/api/analytics/counts/agent-types",
        "/api/analytics/counts/nodes",
    ],
)
def test_every_route_is_403_below_transcript_read(tmp_path: Path, path: str) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    guest = seed_user(hub, username="grace", role=Role.GUEST)
    token = seed_session(hub, guest)

    resp = hub.client.get(path, headers=_cookie(token))
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/api/analytics/events",
        "/api/analytics/events/ndjson",
        "/api/analytics/counts/files",
        "/api/analytics/counts/skills",
        "/api/analytics/counts/agent-types",
        "/api/analytics/counts/nodes",
    ],
)
def test_every_route_refuses_a_runner_principal(tmp_path: Path, path: str) -> None:
    token = _seed_enrolled(tmp_path, runner_id="runner-a")
    hub = build_hub(tmp_path, auth_mode="oauth", runner_auth_mode=RUNNER_AUTH_ENFORCE)

    resp = hub.client.get(path, headers=_bearer(token))
    assert resp.status_code == 403


def test_events_is_200_at_contributor(tmp_path: Path) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)

    resp = hub.client.get("/api/analytics/events", headers=_cookie(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["events"]) == 3
    assert body["next_cursor"] is None


# --- events: filters and paging ---------------------------------------------------


def test_events_filters_by_kind(tmp_path: Path) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)

    resp = hub.client.get("/api/analytics/events", params={"kind": "skill_invocation"}, headers=_cookie(token))

    assert resp.status_code == 200, resp.text
    events = resp.json()["events"]
    assert len(events) == 1
    assert events[0]["subject"] == "wf-commit"
    assert events[0]["payload"] == {"skill_name": "wf-commit"}


def test_events_filters_by_tool(tmp_path: Path) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)

    resp = hub.client.get("/api/analytics/events", params={"tool": "Task"}, headers=_cookie(token))

    assert [e["subject"] for e in resp.json()["events"]] == ["explorer"]


def test_events_filters_by_subject_prefix(tmp_path: Path) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)

    resp = hub.client.get("/api/analytics/events", params={"subject_prefix": "src/"}, headers=_cookie(token))

    assert [e["subject"] for e in resp.json()["events"]] == ["src/a.py"]


def test_events_filters_by_time_range(tmp_path: Path) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)

    resp = hub.client.get(
        "/api/analytics/events",
        params={"since": "2026-08-12T09:30:00Z", "until": "2026-08-12T10:30:00Z"},
        headers=_cookie(token),
    )

    assert [e["subject"] for e in resp.json()["events"]] == ["wf-commit"]


def test_events_pages_with_a_cursor(tmp_path: Path) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)

    first = hub.client.get("/api/analytics/events", params={"limit": 1}, headers=_cookie(token))
    assert first.status_code == 200, first.text
    assert len(first.json()["events"]) == 1
    cursor = first.json()["next_cursor"]
    assert cursor is not None

    second = hub.client.get("/api/analytics/events", params={"limit": 1, "cursor": cursor}, headers=_cookie(token))
    assert len(second.json()["events"]) == 1
    assert second.json()["events"][0]["id"] != first.json()["events"][0]["id"]


@pytest.mark.parametrize("cursor", ["not-an-id", " 2 ", "+3", "1_0", "-1", "", "2.0"])
def test_events_422s_on_a_malformed_cursor(tmp_path: Path, cursor: str) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)

    resp = hub.client.get("/api/analytics/events", params={"cursor": cursor}, headers=_cookie(token))

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "malformed cursor"


def test_a_prior_extractor_version_finds_nothing_under_the_current_one(tmp_path: Path) -> None:
    """D1: mixing versions double-counts the same occurrence — the current version is
    the default, and an explicit stale one reads that version's own (empty) rows."""
    hub, token, _chunk_id = _seeded_hub(tmp_path)

    resp = hub.client.get(
        "/api/analytics/events", params={"extractor_version": f"{EXTRACTOR_VERSION}-stale"}, headers=_cookie(token)
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["events"] == []


# --- the NDJSON encoding: same events, same order -----------------------------------


def test_ndjson_serves_the_same_events_in_the_same_order_as_json(tmp_path: Path) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)

    json_resp = hub.client.get("/api/analytics/events", headers=_cookie(token))
    ndjson_resp = hub.client.get("/api/analytics/events/ndjson", headers=_cookie(token))

    assert ndjson_resp.status_code == 200, ndjson_resp.text
    assert ndjson_resp.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in ndjson_resp.text.strip().split("\n")]
    assert [e["id"] for e in lines] == [e["id"] for e in json_resp.json()["events"]]
    assert [e["subject"] for e in lines] == [e["subject"] for e in json_resp.json()["events"]]


def test_the_ndjson_stream_carries_its_cursor_across_batches(tmp_path: Path) -> None:
    """The batch boundary is the stream's only moving part, and the default 500 puts it
    out of reach of any fixture — so the body is served here one event per batch."""
    hub, token, _chunk_id = _seeded_hub(tmp_path)
    criteria = ScopeFilters(None, None, None, None, None).criteria()

    body = b"".join(ndjson_lines(hub.services.analytics_events, criteria, batch_size=1)).decode()
    lines = [json.loads(line) for line in body.splitlines()]

    whole = hub.client.get("/api/analytics/events", headers=_cookie(token)).json()["events"]
    assert [e["id"] for e in lines] == [e["id"] for e in whole]
    assert len(lines) == 3


# --- the four canned counts, honoring filters ----------------------------------------


def test_counts_by_file(tmp_path: Path) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)

    resp = hub.client.get("/api/analytics/counts/files", headers=_cookie(token))

    assert resp.status_code == 200, resp.text
    assert resp.json()["counts"] == [{"key": "src/a.py", "count": 1}]


def test_counts_by_skill(tmp_path: Path) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)

    resp = hub.client.get("/api/analytics/counts/skills", headers=_cookie(token))

    assert resp.status_code == 200, resp.text
    assert resp.json()["counts"] == [{"key": "wf-commit", "count": 1}]


def test_counts_by_agent_type_is_empty_at_the_main_lane(tmp_path: Path) -> None:
    """The seeded triad's spawn is main-lane (depth 0), so its `agent_type` column
    (the enclosing sidechain's) is unset — the count groups on that column, not the
    spawn's own subject."""
    hub, token, _chunk_id = _seeded_hub(tmp_path)

    resp = hub.client.get("/api/analytics/counts/agent-types", headers=_cookie(token))

    assert resp.status_code == 200, resp.text
    assert resp.json()["counts"] == []


def test_counts_by_agent_type_counts_the_work_done_under_a_sidechain_not_the_spawn(tmp_path: Path) -> None:
    """The discriminating case for this count's column choice: a main-lane ``Task``
    spawning ``explorer`` whose sidechain then reads two files. Grouping on the
    sidechain's ``agent_type`` counts two; grouping on the spawn's subject, one."""
    hub = build_hub(tmp_path, auth_mode="oauth")
    token = seed_session(hub, seed_user(hub, username="ada", role=Role.CONTRIBUTOR))
    chunk_id = _ingest_chunk(hub, headers=_cookie(token))
    spawn = _tool_turn(0, "Task", {"subagent_type": "explorer"}, timestamp="2026-08-12T09:00:00Z")
    spawn["sidechain"] = {
        "agent_id": "ag_1",
        "agent_type": "explorer",
        "link": "resolved",
        "turns": [
            _tool_turn(0, "Read", {"file_path": "src/a.py"}, timestamp="2026-08-12T09:01:00Z"),
            _tool_turn(1, "Read", {"file_path": "src/b.py"}, timestamp="2026-08-12T09:02:00Z"),
        ],
    }
    push = hub.client.post(
        "/api/fleet/transcripts", json={"runner_id": "r1", "records": [_record(chunk_id, turns=[spawn])]}
    )
    assert push.status_code == 200, push.text
    hub.services.event_derivation.sweep()

    resp = hub.client.get("/api/analytics/counts/agent-types", headers=_cookie(token))

    assert resp.status_code == 200, resp.text
    assert resp.json()["counts"] == [{"key": "explorer", "count": 2}]


def test_counts_by_node(tmp_path: Path) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)

    resp = hub.client.get("/api/analytics/counts/nodes", headers=_cookie(token))

    assert resp.status_code == 200, resp.text
    assert resp.json()["counts"] == [{"key": "nd_build", "count": 3}]


def test_counts_by_node_honors_a_kind_filter(tmp_path: Path) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)

    resp = hub.client.get("/api/analytics/counts/nodes", params={"kind": "file_read"}, headers=_cookie(token))

    assert resp.status_code == 200, resp.text
    assert resp.json()["counts"] == [{"key": "nd_build", "count": 1}]
