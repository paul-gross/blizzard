"""The chunk-family refusals answer ``application/problem+json`` with their own type and the same ``detail``
they answered before they had a type (component tier)."""

from __future__ import annotations

from pathlib import Path

from httpx2 import Response
from pydantic import BaseModel

from blizzard.hub.api.problem_declarations_chunks import CHUNKS_DECLARATIONS
from blizzard.wire import problem_types_admin as admin_t
from blizzard.wire import problem_types_chunks as t
from blizzard.wire.chunk import ChunkNotFoundProblem
from blizzard.wire.problem import CHUNK_NOT_FOUND
from blizzard.wire.question import QuestionNotFoundProblem
from tests.support.refusal_observation import declared_refusal_mismatches
from tests.support.scaffolding import HubHarness, build_hub, ingest

_POINTER = {"source": "default", "ref": "12"}


def _observed(
    hub: HubHarness, response: Response, method: str, path: str, status: int, model: type[BaseModel], urn: str
) -> dict[str, object]:
    """Assert ``response`` is the declared problem ``model`` of ``method path`` at ``status``, and return its body."""
    assert hub.app is not None
    assert declared_refusal_mismatches(hub.app.openapi(), response, method, path, status, model) == []
    body = response.json()
    assert body["type"] == urn
    return body


def test_every_declaration_is_in_the_openapi_document(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    paths = hub.app.openapi()["paths"]
    for declaration in CHUNKS_DECLARATIONS:
        responses = paths[declaration.path][declaration.method.lower()]["responses"]
        schema = responses[str(declaration.status_code)]["content"]["application/problem+json"]["schema"]
        assert declaration.model.__name__ in str(schema)


def test_an_unknown_chunk_is_a_chunk_not_found_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/chunks/ch_missing/stop", json={})
    body = _observed(hub, resp, "post", "/api/chunks/{chunk_id}/stop", 404, ChunkNotFoundProblem, CHUNK_NOT_FOUND)
    assert body["detail"] == "unknown chunk ch_missing"


def test_group_into_an_unknown_survivor_is_a_chunk_not_found_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/chunks/ch_missing/group", json={"merge_chunk_ids": ["ch_other"]})
    body = _observed(hub, resp, "post", "/api/chunks/{chunk_id}/group", 404, ChunkNotFoundProblem, CHUNK_NOT_FOUND)
    assert body["detail"] == "unknown chunk ch_missing"


def test_an_empty_ingest_is_an_ingest_empty_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/chunks", json={"tokens": []})
    body = _observed(hub, resp, "post", "/api/chunks", 422, t.IngestEmptyProblem, t.INGEST_EMPTY)
    assert body["detail"] == "at least one token required"


def test_an_unclaimed_token_is_an_ingest_token_unclaimed_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/chunks", json={"tokens": ["nosuch:1"]})
    body = _observed(hub, resp, "post", "/api/chunks", 422, t.IngestTokenUnclaimedProblem, t.INGEST_TOKEN_UNCLAIMED)
    assert str(body["detail"]).startswith("token 'nosuch:1' is not claimed by any configured work source (configured:")


def test_stopping_a_stopped_chunk_is_a_chunk_not_stoppable_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={}).status_code == 202
    resp = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={})
    body = _observed(
        hub, resp, "post", "/api/chunks/{chunk_id}/stop", 409, t.ChunkNotStoppableProblem, t.CHUNK_NOT_STOPPABLE
    )
    assert body["detail"] == f"chunk {chunk_id} is stopped, not stoppable"


def test_detaching_an_unrouted_chunk_is_a_chunk_not_routed_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    resp = hub.client.post(f"/api/chunks/{chunk_id}/detach")
    _observed(hub, resp, "post", "/api/chunks/{chunk_id}/detach", 409, t.ChunkNotRoutedProblem, t.CHUNK_NOT_ROUTED)


def test_requeueing_an_unescalated_chunk_is_a_chunk_not_escalated_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    resp = hub.client.post(f"/api/chunks/{chunk_id}/requeues", json={})
    _observed(
        hub, resp, "post", "/api/chunks/{chunk_id}/requeues", 409, t.ChunkNotEscalatedProblem, t.CHUNK_NOT_ESCALATED
    )


def test_restarting_a_stopped_chunk_is_a_chunk_not_restartable_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={}).status_code == 202
    resp = hub.client.post(f"/api/chunks/{chunk_id}/restart", json={})
    body = _observed(
        hub, resp, "post", "/api/chunks/{chunk_id}/restart", 409, t.ChunkNotRestartableProblem, t.CHUNK_NOT_RESTARTABLE
    )
    assert body["detail"] == f"chunk {chunk_id} is stopped, not restartable"


def test_deleting_a_stopped_chunk_is_a_chunk_not_deletable_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={}).status_code == 202
    resp = hub.client.request("DELETE", f"/api/chunks/{chunk_id}", json={})
    _observed(hub, resp, "delete", "/api/chunks/{chunk_id}", 409, t.ChunkNotDeletableProblem, t.CHUNK_NOT_DELETABLE)


def test_a_self_anchored_promote_is_a_self_anchored_move_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    resp = hub.client.post(f"/api/chunks/{chunk_id}/promote", json={"after_chunk_id": chunk_id})
    body = _observed(
        hub, resp, "post", "/api/chunks/{chunk_id}/promote", 422, t.SelfAnchoredMoveProblem, t.SELF_ANCHORED_MOVE
    )
    assert body["detail"] == "after_chunk_id must not equal chunk_id"


def test_a_contradictory_placement_is_a_contradictory_placement_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    resp = hub.client.post(f"/api/chunks/{chunk_id}/promote", json={"after_chunk_id": "ch_x", "to_top": True})
    body = _observed(
        hub,
        resp,
        "post",
        "/api/chunks/{chunk_id}/promote",
        422,
        t.ContradictoryPlacementProblem,
        t.CONTRADICTORY_PLACEMENT,
    )
    assert body["detail"] == "after_chunk_id and to_top name contradictory placements"


def test_a_queue_naming_a_chunk_twice_is_a_duplicate_reorder_ids_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    resp = hub.client.put("/api/queue", json={"chunk_ids": [chunk_id, chunk_id]})
    body = _observed(hub, resp, "put", "/api/queue", 422, t.DuplicateReorderIdsProblem, t.DUPLICATE_REORDER_IDS)
    assert body["detail"] == "chunk_ids must not repeat"


def test_a_queue_naming_an_unknown_chunk_is_a_not_in_list_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.put("/api/queue", json={"chunk_ids": ["ch_missing"]})
    body = _observed(hub, resp, "put", "/api/queue", 409, t.NotInListProblem, t.NOT_IN_LIST)
    assert body["detail"] == "chunk ch_missing is not in the ready list"


def test_a_blank_edit_value_is_a_blank_edit_value_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"default_effort": "  "})
    _observed(hub, resp, "patch", "/api/chunks/{chunk_id}", 422, t.BlankEditValueProblem, t.BLANK_EDIT_VALUE)


def test_a_duplicate_harness_is_an_invalid_harnesses_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"default_harnesses": ["claude_code", "claude_code"]})
    body = _observed(hub, resp, "patch", "/api/chunks/{chunk_id}", 422, t.InvalidHarnessesProblem, t.INVALID_HARNESSES)
    assert body["detail"] == "default_harnesses entries must be unique"


def test_an_unknown_graph_is_a_graph_not_found_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"graph_id": "gr_missing"})
    body = _observed(
        hub, resp, "patch", "/api/chunks/{chunk_id}", 404, admin_t.GraphNotFoundProblem, admin_t.GRAPH_NOT_FOUND
    )
    assert body["detail"] == "unknown graph gr_missing"


def test_an_unknown_decision_is_a_decision_not_found_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/decisions/dn_missing/resolutions", json={"choice": "x"})
    body = _observed(
        hub,
        resp,
        "post",
        "/api/decisions/{decision_id}/resolutions",
        404,
        t.DecisionNotFoundProblem,
        t.DECISION_NOT_FOUND,
    )
    assert body["detail"] == "unknown decision dn_missing"


def test_an_unknown_question_is_a_question_not_found_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/questions/qn_missing/answers", json={"answer": "x"})
    body = _observed(
        hub,
        resp,
        "post",
        "/api/questions/{question_id}/answers",
        404,
        QuestionNotFoundProblem,
        "urn:blizzard:problem:question-not-found",
    )
    assert body["detail"] == "unknown question qn_missing"


def test_an_unknown_finding_is_a_finding_not_found_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    one = hub.client.get("/api/findings/fd_missing")
    body = _observed(hub, one, "get", "/api/findings/{finding_id}", 404, t.FindingNotFoundProblem, t.FINDING_NOT_FOUND)
    assert body["detail"] == "unknown finding fd_missing"
    verb = hub.client.post("/api/findings/resolve", json={"finding_ids": ["fd_missing"], "note": "done"})
    _observed(hub, verb, "post", "/api/findings/resolve", 404, t.FindingNotFoundProblem, t.FINDING_NOT_FOUND)


def test_an_unknown_flow_is_a_flow_not_found_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/flows/fl_missing")
    body = _observed(hub, resp, "get", "/api/flows/{flow_id}", 404, t.FlowNotFoundProblem, t.FLOW_NOT_FOUND)
    assert body["detail"] == "unknown flow fl_missing"


def test_an_unknown_submission_is_a_submission_not_found_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    resp = hub.client.get(f"/api/chunks/{chunk_id}/flow-submissions/sub_missing/turns")
    body = _observed(
        hub,
        resp,
        "get",
        "/api/chunks/{chunk_id}/flow-submissions/{submission_id}/turns",
        404,
        t.SubmissionNotFoundProblem,
        t.SUBMISSION_NOT_FOUND,
    )
    assert body["detail"] == "unknown submission"
