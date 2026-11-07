"""The runner, host, egress, trace, analytics, spend, transcript and work-item refusals answer typed problems
whose ``detail`` is the text the bare 4xx carried (component tier)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import httpx2
from pydantic import BaseModel

from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.wire import problem_types_runners as t
from blizzard.wire.host import HostRetiredProblem
from blizzard.wire.problem import ProblemDetails
from blizzard.wire.problem_types_runners import (
    EgressBackfillUnavailableProblem,
    EgressBackfillWindowRefusedProblem,
    EgressResetRefusedProblem,
    EgressResetUnavailableProblem,
    HostNotEnrolledProblem,
    HostNotFoundProblem,
    RunnerNotEnrolledProblem,
    RunnerNotRetiredProblem,
    SpendInstantInvalidProblem,
    TraceReplayUnavailableProblem,
    TraceReplayWindowRefusedProblem,
    TranscriptSegmentNotFoundProblem,
)
from blizzard.wire.runner import RunnerNotFoundProblem, UnknownServedProjectProblem
from tests.support.egress_world import egress_hub
from tests.support.refusal_observation import declared_refusal_mismatches
from tests.support.scaffolding import HubHarness, build_hub, pointer_token
from tests.support.trace_hub import trace_hub


def _refused(
    hub: HubHarness, response: httpx2.Response, method: str, path: str, status: int, model: type[BaseModel], type_: str
) -> dict[str, object]:
    assert hub.app is not None
    assert declared_refusal_mismatches(hub.app.openapi(), response, method.lower(), path, status, model) == []
    body = response.json()
    assert body["type"] == type_
    assert body["status"] == status
    assert issubclass(model, ProblemDetails)
    return body


def test_an_unknown_host_is_a_problem_on_every_by_id_verb_and_a_runner_add(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    runners = {"runner_ids": ["rn_x"]}

    for method, path, template, body in (
        ("get", "/api/hosts/hs_nope", "/api/hosts/{host_id}", None),
        ("post", "/api/hosts/hs_nope/adoptions", "/api/hosts/{host_id}/adoptions", runners),
        ("post", "/api/hosts/hs_nope/releases", "/api/hosts/{host_id}/releases", runners),
        ("post", "/api/hosts/hs_nope/enrollments", "/api/hosts/{host_id}/enrollments", None),
        ("post", "/api/hosts/hs_nope/token-revocations", "/api/hosts/{host_id}/token-revocations", {}),
        ("post", "/api/hosts/hs_nope/retire", "/api/hosts/{host_id}/retire", {}),
        ("post", "/api/runners", "/api/runners", {"name": "r", "host_id": "hs_nope"}),
    ):
        resp = hub.client.request(method, path, json=body) if body is not None else hub.client.request(method, path)
        refused = _refused(hub, resp, method, template, 404, HostNotFoundProblem, t.HOST_NOT_FOUND)
        assert refused["detail"] == "unknown host hs_nope"


def test_a_retired_host_refuses_an_enrollment_and_a_runner_add_as_host_retired(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = hub.client.post("/api/hosts", json={"name": "box"}).json()
    hub.client.post(f"/api/hosts/{host['host_id']}/retire", json={"force": True})

    enroll = hub.client.post(f"/api/hosts/{host['host_id']}/enrollments")
    added = hub.client.post("/api/runners", json={"name": "r", "host_id": host["host_id"]})

    for resp, template in ((enroll, "/api/hosts/{host_id}/enrollments"), (added, "/api/runners")):
        refused = _refused(hub, resp, "post", template, 409, HostRetiredProblem, "urn:blizzard:problem:host-retired")
        assert "retired" in str(refused["detail"])


def test_revoking_an_unenrolled_host_token_is_host_not_enrolled(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = hub.client.post("/api/hosts", json={"name": "box"}).json()
    path = f"/api/hosts/{host['host_id']}/token-revocations"
    assert hub.client.post(path, json={}).status_code == 201

    resp = hub.client.post(path, json={})

    refused = _refused(
        hub, resp, "post", "/api/hosts/{host_id}/token-revocations", 409, HostNotEnrolledProblem, t.HOST_NOT_ENROLLED
    )
    assert refused["detail"]


def test_an_unknown_runner_is_a_problem_on_the_runner_verbs(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    for method, suffix in (
        ("get", ""),
        ("post", "/enrollments"),
        ("post", "/retire"),
        ("post", "/reinstate"),
        ("post", "/token-revocations"),
    ):
        resp = hub.client.request(method, f"/api/runners/ghost{suffix}", json=None if method == "get" else {})
        refused = _refused(
            hub,
            resp,
            method,
            f"/api/runners/{{runner_id}}{suffix}",
            404,
            RunnerNotFoundProblem,
            "urn:blizzard:problem:runner-not-found",
        )
        assert refused["detail"] == "unknown runner ghost"


def test_a_runner_naming_an_unknown_project_is_unknown_served_project(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/runners", json={"name": "r", "projects": ["no-such-project"]})

    refused = _refused(
        hub,
        resp,
        "post",
        "/api/runners",
        422,
        UnknownServedProjectProblem,
        "urn:blizzard:problem:unknown-served-project",
    )
    assert "no-such-project" in str(refused["detail"])


def test_reinstating_a_live_runner_and_revoking_an_unenrolled_one_are_typed_conflicts(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    runner_id = hub.client.post("/api/runners", json={"name": "r"}).json()["runner_id"]
    base = f"/api/runners/{runner_id}"

    reinstate = hub.client.post(f"{base}/reinstate", json={"by": "op"})
    _refused(
        hub, reinstate, "post", "/api/runners/{runner_id}/reinstate", 409, RunnerNotRetiredProblem, t.RUNNER_NOT_RETIRED
    )
    assert hub.client.post(f"{base}/token-revocations", json={"by": "op"}).status_code == 201
    revoke = hub.client.post(f"{base}/token-revocations", json={"by": "op"})
    _refused(
        hub,
        revoke,
        "post",
        "/api/runners/{runner_id}/token-revocations",
        409,
        RunnerNotEnrolledProblem,
        t.RUNNER_NOT_ENROLLED,
    )


def test_an_egress_reset_refusal_and_unavailability_are_typed(tmp_path: Path) -> None:
    hub, _ = egress_hub(tmp_path)
    now = hub.clock.now().isoformat()

    off = hub.client.post("/api/egress/reset", json={"dataset": "steps", "to": now})

    refused = _refused(
        hub, off, "post", "/api/egress/reset", 409, EgressResetUnavailableProblem, t.EGRESS_RESET_UNAVAILABLE
    )
    assert "not configured" in str(refused["detail"])


def test_an_egress_reset_of_a_future_instant_is_refused(tmp_path: Path) -> None:
    from blizzard.hub.domain.observability.egress.config import EgressConfig

    config = EgressConfig(directory=tmp_path / "out", settle_seconds=0, datasets=("steps",))
    hub, _ = egress_hub(tmp_path, config)
    future = (hub.clock.now() + timedelta(seconds=1)).isoformat()

    resp = hub.client.post("/api/egress/reset", json={"dataset": "steps", "to": future})

    refused = _refused(hub, resp, "post", "/api/egress/reset", 422, EgressResetRefusedProblem, t.EGRESS_RESET_REFUSED)
    assert "future" in str(refused["detail"])


def test_an_egress_backfill_with_the_export_off_is_unavailable_and_an_inverted_window_is_refused(
    tmp_path: Path,
) -> None:
    hub, _ = egress_hub(tmp_path)
    now = hub.clock.now()
    window = {"since": (now - timedelta(seconds=1)).isoformat(), "until": now.isoformat(), "dataset": "steps"}

    wet = hub.client.post("/api/egress/backfill", json={**window, "dry_run": False})

    refused = _refused(
        hub, wet, "post", "/api/egress/backfill", 409, EgressBackfillUnavailableProblem, t.EGRESS_BACKFILL_UNAVAILABLE
    )
    assert "not configured" in str(refused["detail"])
    inverted = hub.client.post(
        "/api/egress/backfill",
        json={"since": now.isoformat(), "until": now.isoformat(), "dataset": "steps", "dry_run": True},
    )
    refused = _refused(
        hub,
        inverted,
        "post",
        "/api/egress/backfill",
        422,
        EgressBackfillWindowRefusedProblem,
        t.EGRESS_BACKFILL_WINDOW_REFUSED,
    )
    assert "until must be after since" in str(refused["detail"])


def test_a_trace_replay_with_tracing_off_is_unavailable_and_an_inverted_window_is_refused(tmp_path: Path) -> None:
    hub, _ = trace_hub(
        tmp_path,
        trace_exporter=None,
        tracing=TracingConfig(settle_seconds=0, sweep_seconds=60, max_lag_seconds=3600, replay_max_window=3600),
        tracing_settings=TracingSettings.of({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}),
    )
    now = hub.clock.now()
    window = {"since": (now - timedelta(seconds=2)).isoformat(), "until": (now - timedelta(seconds=1)).isoformat()}

    wet = hub.client.post("/api/traces/replay", json=window)

    refused = _refused(
        hub, wet, "post", "/api/traces/replay", 409, TraceReplayUnavailableProblem, t.TRACE_REPLAY_UNAVAILABLE
    )
    assert refused["detail"]
    inverted = hub.client.post(
        "/api/traces/replay", json={"since": now.isoformat(), "until": (now - timedelta(seconds=1)).isoformat()}
    )
    _refused(
        hub, inverted, "post", "/api/traces/replay", 422, TraceReplayWindowRefusedProblem, t.TRACE_REPLAY_WINDOW_REFUSED
    )


def test_a_malformed_spend_instant_is_a_problem_naming_the_field(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/spend", params={"since": "not-a-timestamp"})

    refused = _refused(hub, resp, "get", "/api/spend", 422, SpendInstantInvalidProblem, t.SPEND_INSTANT_INVALID)
    assert refused["detail"] == "since 'not-a-timestamp' is not a valid ISO-8601 instant"


def test_an_unknown_transcript_segment_is_a_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "1"})]}
    ).json()["chunk_id"]

    resp = hub.client.get(f"/api/chunks/{chunk_id}/transcripts/sg_nope")

    refused = _refused(
        hub,
        resp,
        "get",
        "/api/chunks/{chunk_id}/transcripts/{segment_id}",
        404,
        TranscriptSegmentNotFoundProblem,
        t.TRANSCRIPT_SEGMENT_NOT_FOUND,
    )
    assert refused["detail"] == "unknown segment sg_nope"
