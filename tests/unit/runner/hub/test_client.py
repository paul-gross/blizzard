"""The hub client — driven against a fake hub via ``httpx2.MockTransport``.

The runner's outbound edge, no live daemon: peek, the 201/409 claim split, completion,
the idempotent envelope re-read, the chunk poll, and a transport failure surfacing as
``HubClientError``."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TypedDict, Unpack

import httpx2
import pytest
from structlog.testing import capture_logs

from blizzard import __version__
from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.gate_enforceability import UnenforceableGateReason
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.foundation.queue_policy import QueueMatchPolicy
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.foundation.runner_version import VersionRefusalReason
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.runner.harness.capability_snapshot import HarnessCapability
from blizzard.runner.hub.client import (
    ChunkEndedError,
    ChunkNotFoundError,
    ClaimRequest,
    HostAddRefusalReason,
    HostAddRefused,
    HostRunnerRetireRefused,
    HubClientError,
    HubVersionWindow,
    RegistrationReply,
    RunnerAddRefused,
    RunnerVersionRefused,
    TokenIdentity,
    TokenRefusal,
    TranscriptPush,
    UnenforceableGate,
    VersionRefusal,
)
from blizzard.runner.hub.internal import http_hub as http_hub_module
from blizzard.runner.hub.internal.http_hub import HttpHostHubClient, HttpHubClient, HttpHubRunnerAdmin
from blizzard.runner.hub.node_steps import envelope_of
from blizzard.runner.node_steps.envelope import Choice, EnvelopeNode, ProducesSpec, RotateBounds
from blizzard.runner.node_steps.submissions import Completion
from blizzard.wire import problem
from blizzard.wire.envelope import EnvelopeChoice, NodeConfig, NodeEnvelope
from blizzard.wire.graph import ProducesEntry, RotatePolicyView
from blizzard.wire.queue import QueuePeekRequest
from blizzard.wire.runner import RunnerCapability
from blizzard.wire.transcript_segment import TranscriptSegmentBatch, TranscriptSegmentRecord


def _client(handler: Callable[[httpx2.Request], httpx2.Response]) -> HttpHubClient:
    transport = httpx2.MockTransport(handler)
    return HttpHubClient(httpx2.Client(base_url="http://hub.test", transport=transport))


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (404, {"type": problem.TENANT_NOT_FOUND, "detail": "changed"}, HostAddRefusalReason.NO_SUCH_TENANT),
        (409, {"type": problem.TENANT_REQUIRED, "detail": "changed"}, HostAddRefusalReason.TENANT_REQUIRED),
        (
            409,
            {"type": problem.HOST_NAME_TAKEN, "detail": {"code": "tenant_required"}},
            HostAddRefusalReason.NAME_TAKEN,
        ),
        (404, {"detail": "no such tenant"}, HostAddRefusalReason.NO_SUCH_TENANT),
        (409, {"detail": {"code": "tenant_required"}}, HostAddRefusalReason.TENANT_REQUIRED),
        (404, {"detail": "Not Found"}, HostAddRefusalReason.UNSUPPORTED),
        (409, {"detail": "taken"}, HostAddRefusalReason.NAME_TAKEN),
        (409, {"type": problem.RUNNER_ALREADY_HOSTED, "detail": "changed"}, HostAddRefusalReason.NAME_TAKEN),
        (409, {"type": problem.RUNNER_RETIRED, "detail": "changed"}, HostAddRefusalReason.NAME_TAKEN),
        (409, {"type": problem.RUNNER_LIFECYCLE_CONFLICT, "detail": "changed"}, HostAddRefusalReason.NAME_TAKEN),
        (403, {"type": problem.MACHINE_CREDENTIAL_FORBIDDEN, "detail": "changed"}, HostAddRefusalReason.FORBIDDEN),
        (403, {"type": problem.RUNNER_CREDENTIAL_FORBIDDEN, "detail": "changed"}, HostAddRefusalReason.FORBIDDEN),
        (404, {"type": problem.RUNNER_NOT_FOUND, "detail": "changed"}, HostAddRefusalReason.RUNNER_NOT_FOUND),
    ],
)
def test_host_add_refusals(status: int, body: dict[str, object], reason: HostAddRefusalReason) -> None:
    transport = httpx2.MockTransport(lambda request: httpx2.Response(status, json=body))
    client = HttpHubRunnerAdmin(httpx2.Client(base_url="http://hub.test", transport=transport), operator_token=None)
    with pytest.raises(HostAddRefused) as raised:
        client.add_host("host")
    assert raised.value.reason == reason
    if reason == HostAddRefusalReason.RUNNER_NOT_FOUND:
        assert raised.value.detail == "changed"


@pytest.mark.parametrize("kind", [None, "unknown", [], problem.TENANT_REQUIRED])
def test_host_add_unknown_or_wrong_status_type_is_not_legacy(kind: object) -> None:
    transport = httpx2.MockTransport(
        lambda request: httpx2.Response(404, json={"type": kind, "detail": "no such tenant"})
    )
    client = HttpHubRunnerAdmin(httpx2.Client(base_url="http://hub.test", transport=transport), operator_token=None)
    with pytest.raises(HubClientError) as raised:
        client.add_host("host")
    assert type(raised.value) is HubClientError


@pytest.mark.parametrize("kind", [None, "unknown", []])
def test_identity_and_version_unknown_present_type_fail_safely(kind: object) -> None:
    with pytest.raises(HubClientError):
        _client(_refusing(401, json={"type": kind, "reason": "missing"})).identity()
    body = {
        "type": kind,
        "reason": "too-old",
        "runner_version": "0.1",
        "hub_version": "0.3",
        "floor": "0.2",
        "detail": "upgrade",
    }
    with pytest.raises(HubClientError) as raised:
        _client(_refusing(409, json=body)).register_runner("runner", "ws")
    assert type(raised.value) is HubClientError


def test_typed_identity_and_version_refusals() -> None:
    assert _client(_refusing(401, json={"type": problem.RUNNER_IDENTITY, "reason": "missing"})).identity() == (
        TokenRefusal(reason=RunnerTokenRefusalReason.MISSING)
    )
    body = {
        "type": problem.RUNNER_VERSION,
        "reason": "too-old",
        "runner_version": "0.1",
        "hub_version": "0.3",
        "floor": "0.2",
        "detail": "upgrade",
    }
    with pytest.raises(RunnerVersionRefused):
        _client(_refusing(409, json=body)).register_runner("runner", "ws")


@pytest.mark.parametrize(
    ("status", "kind", "error"),
    [
        (409, problem.RUNNER_HOLDS_ROUTES, HostRunnerRetireRefused),
        (409, problem.RUNNER_LIFECYCLE_CONFLICT, HostRunnerRetireRefused),
        (409, problem.RUNNER_RETIRE_INTERRUPTED, HostRunnerRetireRefused),
        (422, problem.UNKNOWN_SERVED_PROJECT, RunnerAddRefused),
    ],
)
def test_host_operation_refusals_respect_type(status: int, kind: str, error: type[HubClientError]) -> None:
    def call(body: dict[str, object]) -> None:
        transport = httpx2.MockTransport(lambda request: httpx2.Response(status, json=body))
        client = HttpHostHubClient(httpx2.Client(base_url="http://hub.test", transport=transport), token="token")
        if status == 409:
            client.retire_runner("runner")
        else:
            client.add_runner("runner")

    bodies: tuple[dict[str, object], ...] = ({"type": kind, "detail": "refused"}, {"detail": "refused"})
    for body in bodies:
        with pytest.raises(error):
            call(body)
    unknown_types: tuple[object, ...] = (None, "unknown", [])
    for unknown in unknown_types:
        with pytest.raises(HubClientError) as raised:
            call({"type": unknown, "detail": "refused"})
        assert type(raised.value) is HubClientError


@pytest.mark.parametrize("typed", [True, False])
def test_host_retire_interrupted_preserves_full_detail(typed: bool) -> None:
    detail = "retirement interrupted: " + "failure " * 100
    body = {"detail": detail}
    if typed:
        body["type"] = problem.RUNNER_RETIRE_INTERRUPTED
    transport = httpx2.MockTransport(lambda request: httpx2.Response(409, json=body))
    client = HttpHostHubClient(httpx2.Client(base_url="http://hub.test", transport=transport), token="token")
    with pytest.raises(HostRunnerRetireRefused) as raised:
        client.retire_runner("runner")
    assert raised.value.detail == detail


def test_envelope_of_carries_every_node_field() -> None:
    node = NodeConfig(
        node_id="nd_build",
        node_name="build",
        executor=Executor.HUB,
        session=SessionMode.FRESH,
        session_source="plan",
        session_name="builders",
        session_model=["opus", "sonnet"],
        session_effort="high",
        session_harnesses=["claude-code", "opencode"],
        session_rotate=RotatePolicyView(max_context_tokens=100, max_transcript_bytes=200, max_invocations=3),
        session_compaction_window="long",
        judged_by=JudgedBy.HUMAN,
        checks=["make test"],
        checks_cwd="sub",
        checks_timeout=42,
        produces=[ProducesEntry(name="diff", kind=ArtifactKind.GIT_COMMIT)],
        retries_max=5,
        choices=[EnvelopeChoice(name="ship", description="ship it", requires_checks=True)],
    )
    wire = NodeEnvelope(chunk_id="ch_1", graph_id="gr_1", epoch=2, node=node, prompt="p", judgement_prompt="j")

    assert envelope_of(wire).node == EnvelopeNode(
        node_id="nd_build",
        node_name="build",
        executor=Executor.HUB,
        session=SessionMode.FRESH,
        judged_by=JudgedBy.HUMAN,
        session_source="plan",
        session_name="builders",
        session_model=["opus", "sonnet"],
        session_effort="high",
        session_harnesses=["claude-code", "opencode"],
        session_rotate=RotateBounds(max_context_tokens=100, max_transcript_bytes=200, max_invocations=3),
        session_compaction_window="long",
        checks=["make test"],
        checks_cwd="sub",
        checks_timeout=42,
        produces=[ProducesSpec(name="diff", kind=ArtifactKind.GIT_COMMIT)],
        retries_max=5,
        choices=[Choice(name="ship", description="ship it", requires_checks=True)],
    )


def test_peek_queue_parses_entries() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/fleet/queue/peek"
        return httpx2.Response(200, json={"entries": [{"chunk_id": "ch_1", "graph_id": "gr_1", "position": 0}]})

    peek = _client(handler).peek_queue((), policy=QueueMatchPolicy.PASS_OVER)
    assert [e.chunk_id for e in peek] == ["ch_1"]


def test_peek_queue_posts_the_request_body() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json={"entries": []})

    _client(handler).peek_queue([HarnessCapability(harness_id="claude", default=True)], policy=QueueMatchPolicy.HOLD)
    expected = QueuePeekRequest(
        capabilities=[RunnerCapability(harness_id="claude", default=True)], policy=QueueMatchPolicy.HOLD
    )
    assert seen["body"] == expected.model_dump(mode="json")


def test_claim_route_201_returns_envelope() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/fleet/routes"
        body = {
            "chunk_id": "ch_1",
            "runner_id": "r1",
            "workspace_id": "ws1",
            "environment_ids": ["e1"],
            "envelope": {
                "chunk_id": "ch_1",
                "graph_id": "gr_1",
                "epoch": 1,
                "node": {
                    "node_id": "nd_build",
                    "node_name": "build",
                    "executor": "runner",
                    "session": "fresh",
                    "judged_by": "worker",
                },
                "prompt": "do work",
                "judgement_prompt": "assess",
            },
            "route_token": "rtok_test",
        }
        return httpx2.Response(201, json=body)

    outcome = _client(handler).claim_route(ClaimRequest(chunk_id="ch_1", workspace_id="ws1", environment_ids=["e1"]))
    assert outcome.won
    assert outcome.claimed is not None
    assert outcome.claimed.envelope.node.node_name == "build"


def test_claim_route_409_is_conflict_not_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(409, json={"chunk_id": "ch_1", "held_by_runner_id": "r2", "detail": "already claimed"})

    outcome = _client(handler).claim_route(ClaimRequest(chunk_id="ch_1", workspace_id="ws1", environment_ids=["e1"]))
    assert not outcome.won
    assert outcome.conflict is not None and outcome.conflict.held_by_runner_id == "r2"


def test_claim_route_409_with_a_status_field_is_a_terminal_denial_not_a_conflict() -> None:
    """The two 409 shapes share a status code but not a body — a
    terminal denial carries ``status``, a race-loss conflict carries
    ``held_by_runner_id`` — and the adapter tells them apart on that field alone."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(409, json={"chunk_id": "ch_1", "status": "stopped", "detail": "chunk is terminal"})

    outcome = _client(handler).claim_route(ClaimRequest(chunk_id="ch_1", workspace_id="ws1", environment_ids=["e1"]))
    assert not outcome.won
    assert outcome.conflict is None
    assert outcome.denied_terminal is not None and outcome.denied_terminal.status == "stopped"


def test_claim_route_409_with_a_prerequisite_chunk_id_field_is_a_dependency_denial_not_a_conflict() -> None:
    """The third 409 shape: a dependency denial carries
    ``prerequisite_chunk_id`` where a terminal denial carries ``status`` and a race-loss
    conflict carries ``held_by_runner_id`` — the adapter tells them apart on that field."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            409,
            json={
                "chunk_id": "ch_1",
                "prerequisite_chunk_id": "ch_0",
                "detail": "chunk depends on an unmet prerequisite",
            },
        )

    outcome = _client(handler).claim_route(ClaimRequest(chunk_id="ch_1", workspace_id="ws1", environment_ids=["e1"]))
    assert not outcome.won
    assert outcome.conflict is None
    assert outcome.denied_terminal is None
    assert outcome.denied_dependency is not None and outcome.denied_dependency.prerequisite_chunk_id == "ch_0"


def test_claim_route_409_with_an_incompatible_runner_id_field_is_an_incompatibility_denial_not_a_conflict() -> None:
    """The fourth 409 shape, told apart from the other three by its
    own ``incompatible_runner_id`` field rather than ``status``, ``prerequisite_chunk_id``,
    or ``held_by_runner_id``."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            409,
            json={
                "chunk_id": "ch_1",
                "incompatible_runner_id": "r1",
                "detail": "runner capabilities no longer satisfy the chunk's reachable lineage",
            },
        )

    outcome = _client(handler).claim_route(ClaimRequest(chunk_id="ch_1", workspace_id="ws1", environment_ids=["e1"]))
    assert not outcome.won
    assert outcome.conflict is None
    assert outcome.denied_terminal is None
    assert outcome.denied_dependency is None
    assert outcome.denied_incompatible is not None and outcome.denied_incompatible.incompatible_runner_id == "r1"


def test_claim_route_403_is_a_paused_denial_not_a_conflict() -> None:
    """A distinct outcome from the 409 race loss: the hub's registry has
    this runner paused and refused the claim outright."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            403, json={"chunk_id": "ch_1", "runner_id": "r1", "detail": "runner is paused at the hub"}
        )

    outcome = _client(handler).claim_route(ClaimRequest(chunk_id="ch_1", workspace_id="ws1", environment_ids=["e1"]))
    assert not outcome.won
    assert outcome.conflict is None
    assert outcome.denied_paused is not None and outcome.denied_paused.runner_id == "r1"


@pytest.mark.parametrize(
    ("status", "kind", "arm"),
    [
        (409, problem.CLAIM_CONFLICT, "conflict"),
        (409, problem.CLAIM_TERMINAL, "denied_terminal"),
        (409, problem.CLAIM_NOT_READY, "denied_terminal"),
        (409, problem.CLAIM_DEPENDENCY, "denied_dependency"),
        (409, problem.CLAIM_INCOMPATIBLE, "denied_incompatible"),
        (403, problem.CLAIM_PAUSED, "denied_paused"),
        (403, problem.CLAIM_RUNNER_RETIRED, "denied_paused"),
        (403, problem.CLAIM_RUNNER_UNREGISTERED, "denied_paused"),
        (403, problem.CLAIM_RUNNER_VERSION_REFUSED, "denied_paused"),
    ],
)
def test_claim_problem_type_takes_precedence(status: int, kind: str, arm: str) -> None:
    body = {
        "type": kind,
        "chunk_id": "ch_1",
        "status": "done",
        "held_by_runner_id": "r2",
        "prerequisite_chunk_id": "ch_0",
        "incompatible_runner_id": "r1",
        "runner_id": "r1",
        "detail": "refused",
    }
    outcome = _client(lambda request: httpx2.Response(status, json=body)).claim_route(
        ClaimRequest(chunk_id="ch_1", workspace_id="ws1", environment_ids=[])
    )
    assert getattr(outcome, arm) is not None


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (409, "unknown"),
        (403, "unknown"),
        (409, None),
        (403, None),
        (403, problem.CLAIM_CONFLICT),
        (409, problem.CLAIM_PAUSED),
    ],
)
def test_claim_unexpected_present_type_fails_safely(status: int, kind: str | None) -> None:
    body = {"type": kind, "chunk_id": "ch_1", "status": "done", "held_by_runner_id": "r2", "runner_id": "r1"}
    with pytest.raises(HubClientError):
        _client(lambda request: httpx2.Response(status, json=body)).claim_route(
            ClaimRequest(chunk_id="ch_1", workspace_id="ws1", environment_ids=[])
        )


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (409, problem.CLAIM_CONFLICT),
        (409, problem.CLAIM_TERMINAL),
        (409, problem.CLAIM_NOT_READY),
        (409, problem.CLAIM_DEPENDENCY),
        (409, problem.CLAIM_INCOMPATIBLE),
        (403, problem.CLAIM_PAUSED),
        (403, problem.CLAIM_RUNNER_RETIRED),
        (403, problem.CLAIM_RUNNER_UNREGISTERED),
        (403, problem.CLAIM_RUNNER_VERSION_REFUSED),
    ],
)
def test_claim_malformed_known_type_fails_as_hub_error(status: int, kind: str) -> None:
    body: dict[str, object] = {"type": kind, "chunk_id": [], "status": [], "held_by_runner_id": [], "runner_id": []}
    with pytest.raises(HubClientError, match=f"POST /fleet/routes -> {status}: malformed refusal"):
        _client(lambda request: httpx2.Response(status, json=body)).claim_route(
            ClaimRequest(chunk_id="ch_1", workspace_id="ws1", environment_ids=[])
        )


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (403, {"runner_id": []}),
        (409, {"status": []}),
        (409, {"prerequisite_chunk_id": []}),
        (409, {"incompatible_runner_id": []}),
        (409, {"held_by_runner_id": []}),
    ],
)
def test_claim_malformed_legacy_shapes_fail_as_hub_error(status: int, body: dict[str, object]) -> None:
    with pytest.raises(HubClientError, match=f"POST /fleet/routes -> {status}: malformed refusal") as error:
        _client(lambda request: httpx2.Response(status, json=body)).claim_route(
            ClaimRequest(chunk_id="ch_1", workspace_id="ws1", environment_ids=[])
        )
    assert error.value.__cause__ is not None


@pytest.mark.parametrize(
    ("status", "body", "arm"),
    [
        (403, {"status": "done", "prerequisite_chunk_id": "p", "incompatible_runner_id": "r"}, "denied_paused"),
        (409, {"status": "done", "prerequisite_chunk_id": "p", "incompatible_runner_id": "r"}, "denied_terminal"),
        (409, {"prerequisite_chunk_id": "p", "incompatible_runner_id": "r"}, "denied_dependency"),
        (409, {"incompatible_runner_id": "r"}, "denied_incompatible"),
    ],
)
def test_claim_legacy_shape_precedence(status: int, body: dict[str, str], arm: str) -> None:
    body.update(chunk_id="ch_1", runner_id="r1", held_by_runner_id="r2", detail="refused")
    outcome = _client(lambda request: httpx2.Response(status, json=body)).claim_route(
        ClaimRequest(chunk_id="ch_1", workspace_id="ws1", environment_ids=[])
    )
    assert getattr(outcome, arm) is not None


@pytest.mark.parametrize("content", [b"not json", b"[]", b"null"])
def test_claim_malformed_refusal_fails_as_hub_error(content: bytes) -> None:
    with pytest.raises(HubClientError):
        _client(lambda request: httpx2.Response(409, content=content)).claim_route(
            ClaimRequest(chunk_id="ch_1", workspace_id="ws1", environment_ids=[])
        )


def test_submit_completion_returns_apply_response() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/fleet/chunks/ch_1/completions"
        return httpx2.Response(200, json={"outcome": "hub_node_taken", "detail": "delivering"})

    resp = _client(handler).submit_completion("ch_1", Completion(choice="pass", epoch=1, from_node_id="nd_build"))
    assert resp.outcome == "hub_node_taken"


def test_push_transcripts_posts_to_its_own_route_not_events() -> None:
    """The transcript lane posts to ``/transcripts``, never ``/events`` — its own structural
    separation, exercised at the wire."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/fleet/transcripts"
        return httpx2.Response(200, json={"runner_id": "r1", "high_water": 3, "applied": [3], "already_applied": []})

    batch = TranscriptSegmentBatch(
        records=[
            TranscriptSegmentRecord(
                seq=3,
                segment_id="seg_1",
                chunk_id="ch_1",
                node_id="nd_build",
                epoch=1,
                spawn_generation=1,
                turn_range_start=0,
                turn_range_end=0,
                final=False,
                normalizer_version="v1",
                harness_version=None,
                turns=[],
            )
        ],
    )
    ack = _client(handler).push_transcripts(_pushes(batch))
    assert (ack.high_water, ack.applied) == (3, [3])


def test_push_transcripts_overrides_the_shared_clients_default_timeout() -> None:
    """`push_transcripts` sends its own short read timeout, while other routes keep the
    shared client's default."""
    seen_timeouts: list[object] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen_timeouts.append(request.extensions["timeout"]["read"])
        return httpx2.Response(200, json={"runner_id": "r1", "high_water": 1, "applied": [1], "already_applied": []})

    transport = httpx2.MockTransport(handler)
    # A much longer default than the transcript route's own override, so the two are
    # unambiguously distinguishable in what the transport actually receives.
    client = HttpHubClient(httpx2.Client(base_url="http://hub.test", transport=transport, timeout=30.0))
    batch = TranscriptSegmentBatch(
        records=[
            TranscriptSegmentRecord(
                seq=1,
                segment_id="seg_1",
                chunk_id="ch_1",
                node_id="nd_build",
                epoch=1,
                spawn_generation=1,
                turn_range_start=0,
                turn_range_end=0,
                final=False,
                normalizer_version="v1",
                harness_version=None,
                turns=[],
            )
        ],
    )
    client.push_transcripts(_pushes(batch))
    client.peek_queue(
        (), policy=QueueMatchPolicy.PASS_OVER
    )  # a plain route, to prove it still rides the client's own default

    assert seen_timeouts[0] == 5.0  # the transcript route's own short override
    assert seen_timeouts[1] == 30.0  # every other route: unaffected, still the shared default


def test_hub_advance_posts_to_the_fleet_path() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/fleet/chunks/ch_1/hub-advance"
        return httpx2.Response(200, json={"chunk_id": "ch_1", "status": "running", "ran": False, "detail": "busy"})

    resp = _client(handler).hub_advance("ch_1")
    assert resp.ran is False


def test_chunk_statuses_parses_the_batch_response() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/fleet/chunk-statuses"
        assert request.url.params.get_list("chunk_id") == ["ch_1", "ch_2"]
        return httpx2.Response(200, json=[{"chunk_id": "ch_1", "status": "done", "latest_epoch": 1}])

    found = _client(handler).chunk_statuses(["ch_1", "ch_2"])
    assert set(found) == {"ch_1"}
    assert found["ch_1"].status == "done"


def test_chunk_statuses_batches_across_the_query_param_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A primed id set larger than the per-request cap becomes more than one GET, each
    within the cap — never one URL whose query string grows with the caller's own id
    count (`src/blizzard/runner/lifecycle/drain.py`'s ``DRAIN_LIMIT`` precedent, applied to the outbound HTTP edge)."""
    monkeypatch.setattr(http_hub_module, "_CHUNK_STATUSES_BATCH_LIMIT", 2)
    seen_batches: list[list[str]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        batch = request.url.params.get_list("chunk_id")
        seen_batches.append(batch)
        return httpx2.Response(200, json=[{"chunk_id": cid, "status": "done", "latest_epoch": 1} for cid in batch])

    found = _client(handler).chunk_statuses(["ch_1", "ch_2", "ch_3"])
    assert seen_batches == [["ch_1", "ch_2"], ["ch_3"]]
    assert set(found) == {"ch_1", "ch_2", "ch_3"}


def test_chunk_statuses_omits_an_id_the_hub_does_not_know() -> None:
    """The batch route never 404s — an unknown id is simply absent from
    the response, unlike the single-chunk-identified reads."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=[])

    assert _client(handler).chunk_statuses(["ch_missing"]) == {}


def test_register_runner_posts_registration() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/fleet/runners"
        import json

        seen.update(json.loads(request.content))
        return httpx2.Response(201, json={"runner_id": "rn_1", "runner_name": "r-claude", "first_registration": True})

    reply = _client(handler).register_runner("r-claude", "ws1", env_capacity=4)
    # The body declares the name and never an id — the hub takes the id from the token; url/redirect_uris,
    # capabilities, and subscriptions default to null/empty when the caller omits them.
    assert reply == RegistrationReply(runner_id="rn_1", runner_name="r-claude", first_registration=True)
    assert seen == {
        "name": "r-claude",
        "projects": None,
        "bindings": None,
        "subscription": None,
        "declining": None,
        "workspace_id": "ws1",
        "env_capacity": 4,
        "agents": None,
        "url": None,
        "redirect_uris": [],
        "capabilities": [],
        "subscriptions": [],
        "gates": [],
        "version": __version__,
    }


def test_register_runner_reads_the_hub_version_and_window_it_answers_with() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert json.loads(request.content)["version"] == "0.2.1"
        return httpx2.Response(
            201,
            json={
                "runner_id": "rn_1",
                "first_registration": True,
                "hub_version": "0.3.0",
                "window": {"floor": "0.2", "ceiling": "0.3"},
            },
        )

    transport = httpx2.MockTransport(handler)
    client = HttpHubClient(httpx2.Client(base_url="http://hub.test", transport=transport), version="0.2.1")
    reply = client.register_runner("r-claude", "ws1")
    assert (reply.hub_version, reply.window) == ("0.3.0", HubVersionWindow(floor="0.2", ceiling="0.3"))


def test_a_registration_the_window_refuses_raises_the_typed_refusal() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            409,
            json={
                "reason": "too-old",
                "runner_version": "0.1.4",
                "hub_version": "0.3.0",
                "floor": "0.2",
                "detail": "upgrade runner 0.1.4 to 0.2 or later",
            },
        )

    with pytest.raises(RunnerVersionRefused) as refused:
        _client(handler).register_runner("r-claude", "ws1")
    assert refused.value.refusal == VersionRefusal(
        reason=VersionRefusalReason.TOO_OLD,
        runner_version="0.1.4",
        hub_version="0.3.0",
        floor="0.2",
        detail="upgrade runner 0.1.4 to 0.2 or later",
    )


def test_register_runner_sends_null_capacity_when_unset() -> None:
    """A caller that omits env_capacity posts an explicit null, not a missing key."""
    seen: dict[str, object] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx2.Response(201, json={"runner_id": "rn_1", "first_registration": True})

    reply = _client(handler).register_runner("r-claude", "ws1")
    # A reply naming no runner leaves the name this registration declared.
    assert reply.runner_name == "r-claude"
    assert seen == {
        "name": "r-claude",
        "projects": None,
        "bindings": None,
        "subscription": None,
        "declining": None,
        "workspace_id": "ws1",
        "env_capacity": None,
        "agents": None,
        "url": None,
        "redirect_uris": [],
        "capabilities": [],
        "subscriptions": [],
        "gates": [],
        "version": __version__,
    }


def test_register_runner_posts_the_gates_it_imposes() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx2.Response(201, json={"runner_id": "r1", "first_registration": True})

    _client(handler).register_runner("r1", "ws1", gates=("build", "review"))
    assert seen["gates"] == ["build", "review"]


def test_register_runner_posts_its_own_federation_identity() -> None:
    """``url``/``redirect_uris`` ride the registration body when given."""
    seen: dict[str, object] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx2.Response(201, json={"runner_id": "r1", "first_registration": True})

    _client(handler).register_runner(
        "r1", "ws1", url="https://runner-a.example", redirect_uris=("https://runner-a.example/api/auth/callback",)
    )
    assert seen["url"] == "https://runner-a.example"
    assert seen["redirect_uris"] == ["https://runner-a.example/api/auth/callback"]


_TENANT = {"tenant_id": "tn_1", "tenant_name": "acme"}


@pytest.mark.parametrize(
    ("answered", "expected"),
    [({"fleet_flags": {"a": True, "b": False}}, {"a": True, "b": False}), ({}, {})],
    ids=["carries-the-flags", "predates-fleet-flags"],
)
def test_identity_carries_the_fleet_flags_the_hub_answers_with(
    answered: dict[str, dict[str, bool]], expected: dict[str, bool]
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"runner_id": "rn_1", "runner_name": "r-claude", **answered})

    identity = _client(handler).identity()
    assert isinstance(identity, TokenIdentity)
    assert dict(identity.fleet_flags) == expected


@pytest.mark.parametrize(
    ("answered", "tenant"),
    [(_TENANT, ("tn_1", "acme")), ({}, (None, None))],
    ids=["names-its-tenant", "predates-tenants"],
)
def test_register_runner_reads_the_tenant_the_hub_answers_with(
    answered: dict[str, str], tenant: tuple[str | None, str | None]
) -> None:
    """The reply's tenant reaches the runner as the hub answered it; a hub that predates tenants
    names none, and the reply carries none rather than failing to parse."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            201, json={"runner_id": "rn_1", "runner_name": "r-claude", "first_registration": False, **answered}
        )

    reply = _client(handler).register_runner("r-claude", "ws1")
    assert (reply.tenant_id, reply.tenant_name) == tenant


def test_fetch_runner_paused_reads_the_derived_brake() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/fleet/runners/r1"
        return httpx2.Response(
            200,
            json={
                "runner_id": "r1",
                "workspace_id": "ws1",
                "registered_at": "2026-07-13T00:00:00+00:00",
                "last_seen_at": "2026-07-13T00:00:00+00:00",
                "online": True,
                "hub_paused": True,
            },
        )

    assert _client(handler).fetch_runner_paused("r1") is True


def test_transport_failure_raises_hub_client_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(500, text="boom")

    with pytest.raises(HubClientError):
        _client(handler).peek_queue((), policy=QueueMatchPolicy.PASS_OVER)


def test_chunk_statuses_5xx_raises_hub_client_error_for_the_whole_call() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(500, text="boom")

    with pytest.raises(HubClientError):
        _client(handler).chunk_statuses(["ch_1"])


def test_chunk_statuses_transport_failure_raises_hub_client_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    with pytest.raises(HubClientError):
        _client(handler).chunk_statuses(["ch_1"])


# --- the chunk-ended 409 decode ------------------------------------ #


class _ResponseBody(TypedDict, total=False):
    json: object
    text: str


def _refusing(status_code: int, **body: Unpack[_ResponseBody]) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status_code, **body)

    return handler


def test_envelope_409_raises_chunk_ended_carrying_the_hub_detail() -> None:
    client = _client(_refusing(409, json={"detail": "chunk ch_1 has no current runner node"}))
    with pytest.raises(ChunkEndedError) as raised:
        client.get_envelope("ch_1")
    assert raised.value.detail == "chunk ch_1 has no current runner node"


def test_route_token_409_raises_chunk_ended_carrying_the_hub_detail() -> None:
    client = _client(_refusing(409, json={"detail": "chunk ch_1 is done"}))
    with pytest.raises(ChunkEndedError) as raised:
        client.rekey_route_token("ch_1")
    assert raised.value.detail == "chunk ch_1 is done"


def test_route_token_404_raises_chunk_ended_carrying_the_hub_detail_without_an_error_log() -> None:
    client = _client(_refusing(404, json={"detail": "chunk ch_1 has no live route"}))
    with capture_logs() as logs, pytest.raises(ChunkEndedError) as raised:
        client.rekey_route_token("ch_1")
    assert raised.value.detail == "chunk ch_1 has no live route"
    assert [entry for entry in logs if entry["log_level"] == "error"] == []


def test_an_unclassified_status_still_logs_a_hub_call_failure_at_error() -> None:
    client = _client(_refusing(500, text="boom"))
    with capture_logs() as logs, pytest.raises(HubClientError):
        client.rekey_route_token("ch_1")
    assert [entry["event"] for entry in logs if entry["log_level"] == "error"] == ["hub call failed"]


def test_chunk_ended_detail_falls_back_to_the_raw_body_when_it_is_not_json() -> None:
    with pytest.raises(ChunkEndedError) as envelope:
        _client(_refusing(409, text="chunk ended")).get_envelope("ch_1")
    with pytest.raises(ChunkEndedError) as rekey:
        _client(_refusing(409, text="route gone")).rekey_route_token("ch_1")
    assert (envelope.value.detail, rekey.value.detail) == ("chunk ended", "route gone")


def test_envelope_404_still_raises_chunk_not_found() -> None:
    with pytest.raises(ChunkNotFoundError):
        _client(_refusing(404, json={"detail": "no chunk ch_1"})).get_envelope("ch_1")


@pytest.mark.parametrize(
    ("operation", "status", "kind", "expected"),
    [
        ("envelope", 404, "chunk-not-found", ChunkNotFoundError),
        ("envelope", 409, "no-current-node", ChunkEndedError),
        ("rekey", 404, "route-not-found", ChunkEndedError),
        ("rekey", 409, "route-terminal", ChunkEndedError),
        ("envelope", 404, "route-not-found", HubClientError),
        ("rekey", 409, "no-current-node", HubClientError),
        ("rekey", 404, "future", HubClientError),
    ],
)
def test_chunk_refusals_require_the_expected_problem_type(
    operation: str, status: int, kind: str, expected: type[HubClientError]
) -> None:
    client = _client(_refusing(status, json={"type": f"urn:blizzard:problem:{kind}", "detail": "gone"}))
    with pytest.raises(expected) as raised:
        if operation == "envelope":
            client.get_envelope("ch_1")
        else:
            client.rekey_route_token("ch_1")
    assert type(raised.value) is expected


@pytest.mark.parametrize(
    "status,kind",
    [(409, "release-refusal"), (403, "route-not-held"), (403, "runner-retired"), (404, "route-not-found")],
)
def test_release_typed_refusal(status: int, kind: str) -> None:
    client = _client(_refusing(status, json={"type": f"urn:blizzard:problem:{kind}", "detail": "no"}))
    assert client.release("ch_1", epoch=1, route_token=None, reason="done") is False


@pytest.mark.parametrize("status", [403, 404, 409])
def test_release_untyped_refusal_remains_false(status: int) -> None:
    client = _client(_refusing(status, json={"detail": "no"}))
    assert client.release("ch_1", epoch=1, route_token=None, reason="done") is False


def test_release_retired_type_on_wrong_status_is_an_error() -> None:
    client = _client(_refusing(409, json={"type": problem.RUNNER_RETIRED, "detail": "no"}))
    with pytest.raises(HubClientError) as raised:
        client.release("ch_1", epoch=1, route_token=None, reason="done")
    assert type(raised.value) is HubClientError


@pytest.mark.parametrize("status", [403, 404, 409])
def test_release_unknown_type_is_an_error(status: int) -> None:
    client = _client(_refusing(status, json={"type": "future", "detail": "no"}))
    with pytest.raises(HubClientError) as raised:
        client.release("ch_1", epoch=1, route_token=None, reason="done")
    assert type(raised.value) is HubClientError


def test_409_on_a_route_that_does_not_opt_in_stays_a_plain_hub_client_error() -> None:
    client = _client(_refusing(409, json={"detail": "chunk ch_1 is done"}))
    with pytest.raises(HubClientError) as raised:
        client.submit_completion("ch_1", Completion(choice="pass", epoch=1, from_node_id="nd_build"))
    assert type(raised.value) is HubClientError


def test_identity_presents_the_runner_token_and_reads_its_runner() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert (request.method, request.url.path) == ("GET", "/api/fleet/identity")
        assert request.headers["Authorization"] == "Bearer runner-token"
        return httpx2.Response(200, json={"runner_id": "rn_1", "runner_name": "r-claude"})

    transport = httpx2.MockTransport(handler)
    client = httpx2.Client(
        base_url="http://hub.test", transport=transport, headers={"Authorization": "Bearer runner-token"}
    )
    assert HttpHubClient(client).identity() == TokenIdentity(runner_id="rn_1", runner_name="r-claude")


@pytest.mark.parametrize(
    ("answered", "tenant"),
    [(_TENANT, ("tn_1", "acme")), ({}, (None, None))],
    ids=["names-its-tenant", "predates-tenants"],
)
def test_identity_reads_the_tenant_the_hub_answers_with(
    answered: dict[str, str], tenant: tuple[str | None, str | None]
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"runner_id": "rn_1", "runner_name": "r-claude", **answered})

    identity = _client(handler).identity()
    assert isinstance(identity, TokenIdentity)
    assert (identity.tenant_id, identity.tenant_name) == tenant


@pytest.mark.parametrize(
    "refusal",
    [
        TokenRefusal(reason=RunnerTokenRefusalReason.MISSING),
        TokenRefusal(reason=RunnerTokenRefusalReason.UNKNOWN),
        TokenRefusal(reason=RunnerTokenRefusalReason.REVOKED, runner_id="rn_1"),
        TokenRefusal(reason=RunnerTokenRefusalReason.RETIRED, runner_id="rn_1"),
    ],
)
def test_identity_returns_the_hubs_typed_refusal(refusal: TokenRefusal) -> None:
    body = {"reason": str(refusal.reason), "runner_id": refusal.runner_id}
    assert _client(_refusing(401, json=body)).identity() == refusal


def test_identity_carries_a_refusal_reason_it_does_not_know_raw() -> None:
    body = {"reason": "gained", "runner_id": None}
    assert _client(_refusing(401, json=body)).identity() == TokenRefusal(reason=Unrecognized("gained"))


def test_an_incompatible_claim_carries_a_denial_reason_it_does_not_know_raw() -> None:
    body = {"chunk_id": "ch_1", "incompatible_runner_id": "r1", "detail": "why", "reason": "gained"}

    def incompatible(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(409, json=body)

    outcome = _client(incompatible).claim_route(
        ClaimRequest(chunk_id="ch_1", workspace_id="ws1", environment_ids=["e1"])
    )
    assert outcome.denied_incompatible is not None and outcome.denied_incompatible.reason == Unrecognized("gained")


@pytest.mark.parametrize(
    ("status_code", "kwargs"),
    [
        (401, {"text": "proxy says no"}),
        (404, {"json": {"detail": "Not Found"}}),
        (500, {"text": "boom"}),
        (200, {"json": {"unexpected": True}}),
    ],
)
def test_identity_raises_on_any_other_answer(status_code: int, kwargs: _ResponseBody) -> None:
    with pytest.raises(HubClientError):
        _client(_refusing(status_code, **kwargs)).identity()


def test_identity_raises_when_the_hub_is_unreachable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    with pytest.raises(HubClientError):
        _client(handler).identity()


def _pushes(batch: TranscriptSegmentBatch) -> list[TranscriptPush]:
    return [TranscriptPush(seq=r.seq, body=r.model_dump(mode="json", exclude={"seq"})) for r in batch.records]


def _register_with(body: dict[str, object]) -> RegistrationReply:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(201, json={"runner_id": "rn_1", "first_registration": True, **body})

    client = HttpHubClient(httpx2.Client(base_url="http://hub.test", transport=httpx2.MockTransport(handler)))
    return client.register_runner("r-claude", "ws1")


def test_register_runner_reads_the_gates_the_hub_reports_as_not_enforceable() -> None:
    reply = _register_with(
        {"unenforceable_gates": [{"node": "deliver", "reason": "hub-executed"}, {"node": "x", "reason": "future"}]}
    )

    assert reply.unenforceable_gates == (
        UnenforceableGate("deliver", UnenforceableGateReason.HUB_EXECUTED),
        UnenforceableGate("x", Unrecognized("future")),
    )


def test_register_runner_reads_no_flags_from_a_hub_that_predates_the_report() -> None:
    assert _register_with({}).unenforceable_gates is None
