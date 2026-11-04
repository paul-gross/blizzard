"""The transcript route's OpenAPI declaration — ``GET /api/leases/{lease_id}/transcript``."""

from __future__ import annotations

from pathlib import Path

from tests.support_transcripts_api import app_with_transcripts


def test_transcript_openapi_declares_the_owner_unavailable_response(tmp_path: Path) -> None:
    app, _store, _repo = app_with_transcripts(tmp_path)

    responses = app.openapi()["paths"]["/runners/{runner_id}/api/leases/{lease_id}/transcript"]["get"]["responses"]

    assert responses["503"]["description"] == "The recorded harness owner is unavailable."
