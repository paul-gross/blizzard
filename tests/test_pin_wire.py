"""Wire-shape pins (unit tier) — decisions a wire model makes by *not* carrying something.

Six wire decisions are invisible to every other test: an absent field, a required field
that could be defaulted, and the exported OpenAPI schema set. Each reversion leaves the
whole suite green while restoring the removed behavior — pinned here (``bzh:mutation-review-selection``).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from blizzard.runner.app import create_app_for_export
from blizzard.wire.attachments import AttachmentRequest
from blizzard.wire.chunk import ChunkDetail, ChunkIngestRequest
from blizzard.wire.decision import DecisionResolutionRequest
from blizzard.wire.git_commits import GitCommitDeclarationRequest
from blizzard.wire.graph import GraphPolicyRequest
from blizzard.wire.history import ChunkHistoryView
from blizzard.wire.transcript_segment import ToolCallSegmentView

pytestmark = pytest.mark.unit


def _runner_schemas() -> dict[str, Any]:
    """The component schemas of the runner's exported OpenAPI spec — the same build the
    exporter (``blizzard.tools.openapi.export``) dumps for the generated TS client."""
    return create_app_for_export().openapi()["components"]["schemas"]


def test_git_commit_declaration_carries_no_forge_field() -> None:
    """The declaration names no forge (issue #143): the origin it is verified against is
    read from the environment's repo manifest, never from the worker — re-adding the
    field re-opens the mismatch class the manifest lookup closed."""
    assert "forge" not in GitCommitDeclarationRequest.model_fields


def test_git_commit_declaration_request_carries_no_scope_field() -> None:
    """The write route has nothing to refuse a graph-scoped body with: the CLI-side
    refusal is the only guard, which only holds while this request model names no scope
    for the CLI to smuggle one through."""
    assert "scope" not in GitCommitDeclarationRequest.model_fields


def test_attachment_request_carries_no_scope_field() -> None:
    """The same pin for ``artifact create``'s write body — a scope field here would
    give ``--scope graph`` a route to reach, defeating the CLI-side refusal."""
    assert "scope" not in AttachmentRequest.model_fields


def test_chunk_ingest_accepts_source_native_tokens_only() -> None:
    """Ingest has exactly one intake shape (``tokens``): a second, pre-resolved
    ``{source, ref}`` field would reintroduce the config-blind guess that resolving
    against the configured sources removes."""
    assert set(ChunkIngestRequest.model_fields) == {"tokens"}


def test_chunk_history_view_requires_all_three_history_lists() -> None:
    """The three fields are required, not defaulted to ``[]`` (issue #237): a hub-side
    rename must fail loudly here rather than decode as "no history yet"."""
    full = {"history": [], "migrations": [], "bounces": []}
    assert ChunkHistoryView.model_validate(full).bounces == []
    for missing in ("history", "migrations", "bounces"):
        with pytest.raises(ValidationError) as caught:
            ChunkHistoryView.model_validate({k: v for k, v in full.items() if k != missing})
        assert caught.value.errors()[0]["type"] == "missing"


def test_graph_policy_request_follow_latest_carries_no_default() -> None:
    """``follow_latest`` is required (issue #164): clearing the override is asked for by
    naming ``null``, never done by an omitted field."""
    assert GraphPolicyRequest.model_fields["follow_latest"].is_required()
    with pytest.raises(ValidationError):
        GraphPolicyRequest()  # type: ignore[call-arg]
    assert GraphPolicyRequest(follow_latest=None).follow_latest is None


def test_chunk_detail_carries_no_transcript_field() -> None:
    """Transcript content only ever leaves via the lazy per-segment reads (blizzard#247,
    D12) — chunk detail's payload size must not grow with a chunk's stored transcript,
    the anti-pattern named against ``hub/api/chunk_views.py``'s own ``_artifacts``."""
    assert "transcript" not in ChunkDetail.model_fields
    assert not [name for name in ChunkDetail.model_fields if "transcript" in name.lower()]


def test_the_lease_history_route_still_answers_a_flat_history_row_view() -> None:
    """The lease-scoped history route still answers a flat, fresh ``HistoryRowView`` (issue #237) — unwidened by
    the chunk-detail proxy's own ``history`` field, which legitimately carries the board's own views (issue #314)."""
    spec = create_app_for_export().openapi()
    schemas = spec["components"]["schemas"]
    route = spec["paths"]["/api/leases/{lease_id}/history"]["get"]
    response_schema = route["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema["items"] == {"$ref": "#/components/schemas/HistoryRowView"}
    assert "HistoryRowView" in schemas
    assert {"TransitionView", "MigrationView", "BounceView"} <= set(schemas)


def test_tool_call_segment_view_defaults_a_missing_input_truncated_to_false() -> None:
    """A turn stored before this field existed is read back through ``_content_view``'s
    ``TurnSegmentView.model_validate``; required here, that read 500s on every such turn."""
    without_field = {
        "name": "Bash",
        "input": {},
        "input_unparsed": None,
        "input_shape": "object",
        "tool_use_id": "tool_1",
        "output": None,
        "output_truncated": False,
    }
    assert ToolCallSegmentView.model_validate(without_field).input_truncated is False


def test_decision_resolution_request_struck_defaults_empty() -> None:
    """A resolution naming no ``struck`` ids passes every proposal — the field must
    default to ``[]``, not be required, or a caller unaware of striking would fail
    every resolve."""
    assert DecisionResolutionRequest(choice="go").struck == []


def test_the_runner_spec_carries_both_escalation_views_under_distinct_names() -> None:
    """The runner serves two differently-shaped escalations — its own status view
    (``runner_status.EscalationView``, ``resume_command``) and the chunk's on the proxied
    ``ChunkDetail`` (``ChunkEscalationView``). Distinct names keep both un-mangled in the client."""
    schemas = _runner_schemas()
    assert not [name for name in schemas if "__" in name]  # a collision mangles both names
    assert "resume_command" in schemas["EscalationView"]["properties"]
    assert "takeover_command" in schemas["ChunkEscalationView"]["properties"]
    assert "ChunkDetail" in schemas and "ChunkDetailView" not in schemas
    assert {"history", "artifacts", "escalation"} <= set(schemas["ChunkDetail"]["properties"])


def test_the_runner_spec_serves_the_shared_segment_turn_shape_not_a_retired_turn_view() -> None:
    """``TurnView`` is retired (blizzard#248 D1): the runner's lease-transcript route now
    serves ``TurnSegmentView`` — the same turn shape the hub's segment-content route
    serves — so a regenerated client never grows a second, parallel turn model."""
    schemas = _runner_schemas()
    assert "TurnView" not in schemas
    assert "TurnSegmentView" in schemas
    turn_props = schemas["TurnSegmentView"]["properties"]
    assert {"tool", "thinking_redacted", "sidechain"} <= set(turn_props)
    assert set(schemas["TranscriptResponse"]["properties"]["turns"]["items"]) == {"$ref"}
    assert schemas["TranscriptResponse"]["properties"]["turns"]["items"]["$ref"].endswith("TurnSegmentView")
