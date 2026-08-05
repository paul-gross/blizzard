"""Wire-shape pins (unit tier) — decisions a wire model makes by *not* carrying something.

Five wire decisions are invisible to every other test: an absent field, a required field
that could be defaulted, and the exported OpenAPI schema set. Each reversion leaves the
whole suite green while restoring the removed behavior — pinned here (``bzh:mutation-review-selection``).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from blizzard.runner.app import create_app_for_export
from blizzard.wire.chunk import ChunkIngestRequest
from blizzard.wire.git_commits import GitCommitDeclarationRequest
from blizzard.wire.graph import GraphPolicyRequest
from blizzard.wire.history import ChunkHistoryView

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


def test_the_runner_spec_carries_no_chunk_detail_history_views() -> None:
    """The history route answers with a flat, fresh ``HistoryRowView`` (issue #237), so
    the board's own transition/migration/bounce views stay out of the runner's spec."""
    schemas = _runner_schemas()
    assert not {"TransitionView", "MigrationView", "BounceView"} & set(schemas)
    assert "HistoryRowView" in schemas


def test_the_runner_spec_escalation_view_is_the_runners_own() -> None:
    """``ChunkHeaderView`` projects the proxied ``ChunkDetail`` down (issue #185), so the
    runner's one ``EscalationView`` is ``wire.runner_status``' status view — pulling
    ``wire.chunk``'s identically-named view in collides and mangles the generated client."""
    schemas = _runner_schemas()
    assert not [name for name in schemas if "__" in name]  # a collision mangles both names
    assert "resume_command" in schemas["EscalationView"]["properties"]
    assert "ChunkHeaderView" in schemas and "ChunkDetail" not in schemas
