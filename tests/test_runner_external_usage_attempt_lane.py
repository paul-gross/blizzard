"""``ExternalUsageAttemptLane`` — which lane the host's sampling pass records an attempt on (unit tier): a registered
host's own lane, else its first registered runner's attempt handle with the fact announced, else nowhere."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from structlog.testing import capture_logs

from blizzard.runner.events.broker import EventBroker, RunnerEventPublisher
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.hub.host_registration import HostRegistrationHolder
from blizzard.runner.hub.identity import RunnerIdentity, RunnerIdentityHolder
from blizzard.runner.usage.external_usage_sample import ExternalUsageAttemptLane

pytestmark = pytest.mark.unit

_AT = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
_KIND = "external_subscription_usage.sampled"
_ATTEMPT: dict[str, Any] = {
    "slug": "anthropic",
    "sampled_at": _AT,
    "payload": "{}",
    "report_kind": _KIND,
    "report_payload": "{}",
    "miss_reason": None,
}


class _RecordedWrites:
    """A lane write that remembers each attempt it was handed, answering ``seq`` for the buffered report."""

    def __init__(self, seq: int | None = 7) -> None:
        self.seq = seq
        self.attempts: list[dict[str, Any]] = []

    def record_external_usage_attempt(self, **attempt: Any) -> int | None:
        self.attempts.append(attempt)
        return self.seq

    def record_host_external_usage_attempt(self, **attempt: Any) -> int:
        self.attempts.append(attempt)
        return self.seq or 0


@dataclass
class _Runner:
    identity: RunnerIdentityHolder
    events: IRunnerEventPublisher | None
    external_usage_attempts: _RecordedWrites = field(default_factory=_RecordedWrites)


def _runner(runner_id: str, broker: EventBroker, *, registered: bool) -> _Runner:
    identity = RunnerIdentity(runner_id, runner_id, _AT) if registered else None
    return _Runner(RunnerIdentityHolder(identity), RunnerEventPublisher.over(broker, runner_id))


def _lane(host_lane: _RecordedWrites, runners: list[_Runner], *, registered: bool) -> ExternalUsageAttemptLane:
    return ExternalUsageAttemptLane(
        host_lane, lambda: runners, registration=HostRegistrationHolder.for_host(registered=registered)
    )


def _announced(broker: EventBroker) -> list[dict[str, Any]]:
    return [json.loads(event.data) for event in broker.snapshot() if event.type == "fact-changed"]


def test_a_registered_hosts_attempt_rides_its_own_lane_and_no_runners() -> None:
    broker = EventBroker()
    host_lane = _RecordedWrites()
    runner = _runner("rn_a", broker, registered=True)
    _lane(host_lane, [runner], registered=True).record(**_ATTEMPT)
    assert host_lane.attempts == [_ATTEMPT]
    assert runner.external_usage_attempts.attempts == []
    assert _announced(broker) == []


def test_an_unregistered_hosts_attempt_rides_its_first_registered_runners_handle_and_is_announced() -> None:
    broker = EventBroker()
    host_lane = _RecordedWrites()
    unregistered = _runner("rn_a", broker, registered=False)
    first = _runner("rn_b", broker, registered=True)
    later = _runner("rn_c", broker, registered=True)
    _lane(host_lane, [unregistered, first, later], registered=False).record(**_ATTEMPT)
    assert host_lane.attempts == []
    assert unregistered.external_usage_attempts.attempts == later.external_usage_attempts.attempts == []
    assert first.external_usage_attempts.attempts == [_ATTEMPT]
    assert [(frame["kind"], frame["seq"], frame["runner_id"]) for frame in _announced(broker)] == [(_KIND, 7, "rn_b")]


def test_an_attempt_buffering_no_report_is_recorded_unannounced() -> None:
    broker = EventBroker()
    runner = _runner("rn_a", broker, registered=True)
    runner.external_usage_attempts.seq = None
    _lane(_RecordedWrites(), [runner], registered=False).record(**_ATTEMPT)
    assert runner.external_usage_attempts.attempts == [_ATTEMPT]
    assert _announced(broker) == []


def test_an_unregistered_hosts_attempt_with_no_registered_runner_is_dropped_with_a_warning() -> None:
    broker = EventBroker()
    host_lane = _RecordedWrites()
    runner = _runner("rn_a", broker, registered=False)
    with capture_logs() as logs:
        _lane(host_lane, [runner], registered=False).record(**_ATTEMPT)
    assert host_lane.attempts == runner.external_usage_attempts.attempts == []
    assert _announced(broker) == []
    assert [(log["event"], log["slug"]) for log in logs if log["log_level"] == "warning"] == [
        ("external usage attempt not recorded: no runner of the host has registered", "anthropic")
    ]
