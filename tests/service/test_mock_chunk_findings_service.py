"""``GET /api/fleet/chunks/{id}/findings`` and its ``/{finding_id}`` sibling — proven
against a real ``blizzard-mock-hub`` subprocess (blizzard#397 Phase 1), over an actual
process boundary rather than the in-process FastAPI test client
``tests/test_fleet_chunk_findings_api.py`` uses. Mirrors
``tests/service/test_system_artifacts_service.py``'s bare-``mock_hub``-client shape: no
runner is needed to prove the mock itself serves the two routes."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.test_acceptance_loop import _free_port
from tests.service.support import mock_hub, mock_hub_chunk_spec, require_mock_fleet, service_gate

pytestmark = [pytest.mark.service, service_gate]

_WORK_REF_URL = "https://example.invalid/issues/1"


def _answered_chunk_spec(work_ref: str) -> dict:
    spec = mock_hub_chunk_spec(work_ref)
    spec["garden_answered_findings"] = [
        {
            "finding_id": "fin_1",
            "routine_name": "nightly",
            "scope_slug": "blizzard",
            "class": "stale-docstring",
            "locus": "a.py:1",
            "summary": "s",
        },
    ]
    return spec


def test_the_mock_hub_serves_the_chunk_findings_list_and_get_routes(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    hub_port = _free_port()

    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/chunk", json=_answered_chunk_spec(_WORK_REF_URL))
        assert seeded.status_code == 201, seeded.text
        chunk_id = seeded.json()["chunk_id"]

        listed = hub.get(f"/api/fleet/chunks/{chunk_id}/findings")
        assert listed.status_code == 200, listed.text
        assert [row["finding_id"] for row in listed.json()] == ["fin_1"]

        got = hub.get(f"/api/fleet/chunks/{chunk_id}/findings/fin_1")
        assert got.status_code == 200, got.text
        assert got.json()["finding_id"] == "fin_1"


def test_the_mock_hub_404s_a_chunk_answering_no_proposal(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    hub_port = _free_port()

    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/chunk", json=mock_hub_chunk_spec(_WORK_REF_URL))  # no garden_answered_findings
        assert seeded.status_code == 201, seeded.text
        chunk_id = seeded.json()["chunk_id"]

        resp = hub.get(f"/api/fleet/chunks/{chunk_id}/findings")
        assert resp.status_code == 404, resp.text
        assert "no accepted, minted garden proposal" in resp.json()["detail"]
