from datetime import UTC, datetime

import pytest

from blizzard.foundation.host_registration_state import HostRegistrationState
from blizzard.runner.hub.client import HostFactPushAck, HostRecord, HostRefusalReason, HostRefused
from blizzard.runner.hub.host_registration import HostRegistrationStatus, status_after_refusal
from blizzard.runner.lifecycle.host_lane_drain import host_lane_mark


@pytest.mark.parametrize(("tenant", "expected"), [("tenant-a", True), ("tenant-b", False), (None, True)])
def test_host_record_serves(tenant: str | None, expected: bool) -> None:
    record = HostRecord(host_id="host", tenant_id="tenant-a", host_name="name")
    assert record.serves(tenant) is expected


@pytest.mark.parametrize(
    ("reason", "state", "unknown"),
    [
        (HostRefusalReason.VERSION_REFUSED, HostRegistrationState.VERSION_REFUSED, False),
        (HostRefusalReason.NOT_FOUND, HostRegistrationState.NO_HOSTS, True),
        (HostRefusalReason.CREDENTIAL_REFUSED, HostRegistrationState.REFUSED, True),
        (HostRefusalReason.RETIRED, HostRegistrationState.REFUSED, True),
    ],
)
def test_status_after_refusal(reason: HostRefusalReason, state: HostRegistrationState, unknown: bool) -> None:
    answered_at = datetime(2025, 1, 1, tzinfo=UTC)
    prior = HostRegistrationStatus(
        state=HostRegistrationState.REGISTERED, answered_at=answered_at, host_name="host", held_runner_ids=("runner",)
    )
    status, standings_unknown = status_after_refusal(HostRefused(reason, 409, "detail"), prior)
    assert standings_unknown is unknown
    assert status == HostRegistrationStatus(
        state=state,
        answered_at=None if unknown else answered_at,
        host_name=None if unknown else "host",
        held_runner_ids=() if unknown else ("runner",),
        detail="409: detail" if unknown else "detail",
    )


@pytest.mark.parametrize(("rejected", "expected"), [([], 5), ([1, 4], 5), ([2, 8, 6], 8)])
def test_host_lane_mark(rejected: list[int], expected: int) -> None:
    ack = HostFactPushAck(high_water=5, applied=[], already_applied=[], rejected=rejected)
    assert host_lane_mark(ack) == expected
