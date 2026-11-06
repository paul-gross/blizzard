"""The runner identity service and the retirement rule (unit tier): a joined runner's row is built and recorded
through the service, and a runner's row is forgotten on the hub's proof of its retirement alone."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.runner.hub.bootstrap import JoinedRunner
from blizzard.runner.hub.client import HubClientError, TokenIdentity, TokenRefusal
from blizzard.runner.hub.identity import RunnerIdentity, proves_retired
from blizzard.runner.hub.identity_service import RetiredRunnerWatch, RunnerIdentityService
from tests.support.runner_init_fakes import FakeTokenIdentityReader

_AT = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
_RETIRED = TokenRefusal(RunnerTokenRefusalReason.RETIRED, runner_id="rn_a")


@dataclass
class _Rows:
    row: RunnerIdentity | None = None
    forgotten: int = 0

    def runner_identity(self) -> RunnerIdentity | None:
        return self.row

    def record_runner_identity(self, identity: RunnerIdentity) -> None:
        self.row = identity

    def forget_runner_identity(self) -> None:
        self.row = None
        self.forgotten += 1


@dataclass
class _Watched:
    rows: _Rows = field(default_factory=_Rows)

    def watch(
        self, answer: TokenIdentity | TokenRefusal | HubClientError
    ) -> tuple[RetiredRunnerWatch, FakeTokenIdentityReader]:
        reader = FakeTokenIdentityReader(answer)
        return RetiredRunnerWatch(runner_id="rn_a", rows=self.rows, forgetter=self.rows, token_identity=reader), reader


def test_a_joined_runners_row_is_recorded_from_the_join_and_the_clock() -> None:
    rows = _Rows()
    joined = JoinedRunner(runner_id="rn_a", runner_name="a", added=True, tenant_id="tn_1", tenant_name="One")

    RunnerIdentityService(recorder=rows, forgetter=rows, clock=FixedClock(_AT)).record(joined)

    assert rows.row == RunnerIdentity("rn_a", "a", _AT, tenant_id="tn_1", tenant_name="One")


def test_forgetting_deletes_the_row_and_forgetting_none_is_not_an_error() -> None:
    rows = _Rows(row=RunnerIdentity("rn_a", "a", _AT))
    service = RunnerIdentityService(recorder=rows, forgetter=rows, clock=FixedClock(_AT))

    service.forget()
    service.forget()

    assert rows.row is None


@pytest.mark.parametrize(
    ("answer", "proves"),
    [
        (TokenRefusal(RunnerTokenRefusalReason.RETIRED, runner_id="rn_a"), True),
        (TokenRefusal(RunnerTokenRefusalReason.RETIRED, runner_id="rn_b"), False),
        (TokenRefusal(RunnerTokenRefusalReason.RETIRED), False),
        (TokenRefusal(RunnerTokenRefusalReason.REVOKED, runner_id="rn_a"), False),
        (TokenRefusal(RunnerTokenRefusalReason.UNKNOWN), False),
        (TokenIdentity(runner_id="rn_a", runner_name="a"), False),
    ],
)
def test_only_a_retired_refusal_naming_the_runner_proves_it_retired(
    answer: TokenIdentity | TokenRefusal, proves: bool
) -> None:
    assert proves_retired(answer, "rn_a") is proves


def test_a_retired_answer_forgets_the_row() -> None:
    watched = _Watched(_Rows(row=RunnerIdentity("rn_a", "a", _AT)))
    watch, _ = watched.watch(_RETIRED)

    assert watch.forget_if_retired() is True
    assert watched.rows.row is None


@pytest.mark.parametrize(
    "answer",
    [
        TokenRefusal(RunnerTokenRefusalReason.RETIRED, runner_id="rn_b"),
        TokenRefusal(RunnerTokenRefusalReason.REVOKED, runner_id="rn_a"),
        TokenRefusal(RunnerTokenRefusalReason.UNKNOWN),
        TokenIdentity(runner_id="rn_a", runner_name="a"),
        HubClientError("outage"),
    ],
)
def test_any_other_answer_or_an_outage_leaves_the_row(answer: TokenIdentity | TokenRefusal | HubClientError) -> None:
    watched = _Watched(_Rows(row=RunnerIdentity("rn_a", "a", _AT)))
    watch, _ = watched.watch(answer)

    assert watch.forget_if_retired() is False
    assert watched.rows.row is not None


def test_a_runner_with_no_row_costs_no_hub_call() -> None:
    watch, reader = _Watched().watch(_RETIRED)

    assert watch.forget_if_retired() is False
    assert reader.calls == 0
