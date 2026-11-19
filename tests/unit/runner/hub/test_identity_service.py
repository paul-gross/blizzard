"""The retired-runner watch and the retirement rule (unit tier): a runner is recorded retired on the hub's proof of its
retirement alone, and its identity row stays."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.runner.hub.client import HubClientError, TokenIdentity, TokenRefusal
from blizzard.runner.hub.identity import RunnerIdentity, proves_retired
from blizzard.runner.hub.identity_service import RetiredRunnerWatch
from tests.support.runner_init_fakes import FakeTokenIdentityReader

_AT = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
_RETIRED = TokenRefusal(RunnerTokenRefusalReason.RETIRED, runner_id="rn_a")


@dataclass
class _Rows:
    row: RunnerIdentity | None = None

    def runner_identity(self) -> RunnerIdentity | None:
        return self.row


@dataclass
class _Retirement:
    recorded: int = 0
    already: bool = False

    def record_retired(self) -> bool:
        if self.already:
            return False
        self.already = True
        self.recorded += 1
        return True


@dataclass
class _Watched:
    rows: _Rows = field(default_factory=_Rows)
    retirement: _Retirement = field(default_factory=_Retirement)

    def watch(
        self, answer: TokenIdentity | TokenRefusal | HubClientError
    ) -> tuple[RetiredRunnerWatch, FakeTokenIdentityReader]:
        reader = FakeTokenIdentityReader(answer)
        watch = RetiredRunnerWatch(runner_id="rn_a", rows=self.rows, retirement=self.retirement, token_identity=reader)
        return watch, reader


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


def test_a_retired_answer_records_the_retirement_and_keeps_the_row() -> None:
    watched = _Watched(_Rows(row=RunnerIdentity("rn_a", "a", _AT)))
    watch, _ = watched.watch(_RETIRED)

    assert watch.record_if_retired() is True
    assert watched.retirement.recorded == 1
    assert watched.rows.row is not None


def test_a_runner_already_retired_records_nothing_more() -> None:
    watched = _Watched(_Rows(row=RunnerIdentity("rn_a", "a", _AT)), _Retirement(already=True))
    watch, _ = watched.watch(_RETIRED)

    assert watch.record_if_retired() is False
    assert watched.retirement.recorded == 0


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
def test_any_other_answer_or_an_outage_records_nothing(answer: TokenIdentity | TokenRefusal | HubClientError) -> None:
    watched = _Watched(_Rows(row=RunnerIdentity("rn_a", "a", _AT)))
    watch, _ = watched.watch(answer)

    assert watch.record_if_retired() is False
    assert watched.retirement.recorded == 0


def test_a_runner_with_no_row_costs_no_hub_call() -> None:
    watch, reader = _Watched().watch(_RETIRED)

    assert watch.record_if_retired() is False
    assert reader.calls == 0
