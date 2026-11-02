"""Retirement refusals at the API edge."""

import json
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from blizzard.hub.api.fleet import claim_route
from blizzard.hub.api.runners import enroll_runner
from blizzard.hub.domain.runners.host import HostRetired
from blizzard.hub.domain.runners.registration import RunnerRetired
from blizzard.wire.route import RouteClaim

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "error", [HostRetired("host-1", action="enrollment"), RunnerRetired("runner-1", action="enrollment")]
)
def test_enrollment_maps_retirement_to_conflict(error: Exception) -> None:
    services = MagicMock()
    services.enrollment.enroll.side_effect = error

    with pytest.raises(HTTPException) as refused:
        enroll_runner("runner-1", services)

    assert refused.value.status_code == 409
    assert refused.value.detail == str(error)


@pytest.mark.parametrize("error", [HostRetired("host-1", action="claim"), RunnerRetired("runner-1", action="claim")])
def test_claim_maps_locked_retirement_to_typed_denial(error: Exception) -> None:
    services = MagicMock()
    services.chunks.facts.load_facts.return_value = None
    services.claim.claim.side_effect = error
    fleet = MagicMock()
    fleet.caller_id.return_value = "runner-1"
    claim = RouteClaim(chunk_id="chunk-1", workspace_id="workspace-1", environment_ids=[])

    response = claim_route(claim, services, fleet)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 403
    assert json.loads(bytes(response.body)) == {"chunk_id": "chunk-1", "runner_id": "runner-1", "detail": str(error)}
    services.claim.claim.assert_called_once()
    services.events.publish_queue_changed.assert_not_called()
