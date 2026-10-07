"""An exited worker's judgement rules, pinned by value — no store, no clock, no hub.

Exit routing (gate, nudge, launch, and the judge-resume entry), the collect precedence over
an in-flight elicitation, the verdict against the node's choices and checks, the check plan,
and which leases are judgeable at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.asks import OpenAsk
from blizzard.runner.leases.elicitation import ELICITATION_STALENESS_THRESHOLD, PendingElicitation
from blizzard.runner.leases.model import Lease
from blizzard.runner.lifecycle.judgement.check_runner import DEFAULT_CHECK_TIMEOUT
from blizzard.runner.lifecycle.judgement.checks import CheckPlan, ExecutedCheck
from blizzard.runner.lifecycle.judgement.collect import CollectOutcome, ElicitationExit
from blizzard.runner.lifecycle.judgement.exit_route import (
    ExitEntry,
    ExitNotJudgeable,
    ExitRoute,
    JudgeableExit,
    route_exit,
)
from blizzard.runner.lifecycle.judgement.verdict import Verdict, VerdictOutcome
from blizzard.runner.node_steps.envelope import Choice
from tests.runner_fakes import make_envelope

pytestmark = pytest.mark.unit

_LAUNCHED = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_FRESH = _LAUNCHED + timedelta(minutes=1)
_STALE = _LAUNCHED + ELICITATION_STALENESS_THRESHOLD + timedelta(seconds=1)
_CHOICES = [
    Choice(name="pass", description="meets criteria", requires_checks=True),
    Choice(name="fail", description="does not"),
]
_ASK = OpenAsk(
    lease_id="lease_1",
    chunk_id="ch_1",
    question_id="q_1",
    question="which way?",
    options=["a", "b"],
    session_id="sess-a",
    asked_at=_LAUNCHED,
)
_GREEN = ExecutedCheck(command="mise run lint", passed=True, output_tail="")
_RED = ExecutedCheck(command="mise run test", passed=False, output_tail="1 failed")


def _lease(*, session_id: str | None = "sess-a") -> Lease:
    return Lease(
        lease_id="lease_1",
        chunk_id="ch_1",
        graph_id="gr_1",
        node_id="nd_build",
        node_name="build",
        epoch=1,
        retries_max=2,
        created_at=_LAUNCHED,
        session_id=session_id,
        harness_id=CLAUDE_CODE_HARNESS_ID if session_id is not None else None,
    )


def _record(relaunch_count: int = 0) -> PendingElicitation:
    return PendingElicitation(
        id=1,
        lease_id="lease_1",
        epoch=1,
        pid=100,
        process_start_time="start-100",
        pgid=100,
        output_path="/tmp/lease_1.1.0.elicitation",
        first_launched_at=_LAUNCHED,
        relaunch_count=relaunch_count,
    )


# --- exit routing -------------------------------------------------------------------------


def test_route_gate_beats_nudge() -> None:
    assert route_exit(ExitEntry.EXIT, gated=True) is ExitRoute.BUFFER_DECISION
    assert route_exit(ExitEntry.EXIT, gated=True, produces_unmet=True, nudge_spent=False) is ExitRoute.BUFFER_DECISION


def test_route_asks_for_the_produces_state_before_nudging() -> None:
    assert route_exit(ExitEntry.EXIT, gated=False) is ExitRoute.RECONCILE_PRODUCES


def test_route_missing_unspent_nudges() -> None:
    assert route_exit(ExitEntry.EXIT, gated=False, produces_unmet=True, nudge_spent=False) is ExitRoute.NUDGE


def test_route_spent_nudge_elicits() -> None:
    assert route_exit(ExitEntry.EXIT, gated=False, produces_unmet=True, nudge_spent=True) is ExitRoute.ELICIT


def test_route_met_produces_elicits() -> None:
    assert route_exit(ExitEntry.EXIT, gated=False, produces_unmet=False) is ExitRoute.ELICIT


@pytest.mark.parametrize("gated", [True, False])
@pytest.mark.parametrize("produces_unmet", [None, True, False])
def test_judge_resume_only_elicits(gated: bool, produces_unmet: bool | None) -> None:
    assert route_exit(ExitEntry.JUDGE_RESUME, gated=gated, produces_unmet=produces_unmet) is ExitRoute.ELICIT


def test_sessionless_lease_not_judgeable() -> None:
    with pytest.raises(ExitNotJudgeable, match="lease lease_1 has no recorded session"):
        JudgeableExit.of(_lease(session_id=None))


def test_a_lease_with_a_session_is_judgeable_against_it() -> None:
    judgeable = JudgeableExit.of(_lease())
    assert judgeable.session == SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a")


# --- collect precedence -------------------------------------------------------------------


def test_a_live_elicitation_under_the_bound_waits() -> None:
    assert ElicitationExit(_record(), _FRESH, alive=True).decide() is CollectOutcome.WAIT


def test_a_live_elicitation_past_the_bound_fails() -> None:
    assert ElicitationExit(_record(), _STALE, alive=True).decide() is CollectOutcome.FAIL_STALE


def test_an_exited_elicitation_is_classified_first() -> None:
    assert ElicitationExit(_record(), _STALE, alive=False).decide() is CollectOutcome.CLASSIFY


def test_collect_usage_limit_beats_overload() -> None:
    exited = ElicitationExit(_record(), _FRESH, alive=False)
    assert exited.decide(usage_limited=True, overload_backing_off=True, output_present=True) is CollectOutcome.PARK


def test_usage_limit_beats_staleness() -> None:
    exited = ElicitationExit(_record(), _STALE, alive=False)
    assert exited.decide(usage_limited=True) is CollectOutcome.PARK


def test_overload_beats_staleness() -> None:
    exited = ElicitationExit(_record(), _STALE, alive=False)
    assert exited.decide(usage_limited=False, overload_backing_off=True) is CollectOutcome.BACK_OFF


def test_stale_exited_fails() -> None:
    exited = ElicitationExit(_record(), _STALE, alive=False)
    assert exited.decide(usage_limited=False, output_present=True, usable=True) is CollectOutcome.FAIL_STALE


def test_lost_output_relaunches_under_bound() -> None:
    exited = ElicitationExit(_record(), _FRESH, alive=False)
    assert exited.decide(usage_limited=False, output_present=False) is CollectOutcome.RELAUNCH


def test_present_output_asks_for_the_harness_verdict() -> None:
    exited = ElicitationExit(_record(), _FRESH, alive=False)
    assert exited.decide(usage_limited=False, output_present=True) is CollectOutcome.READ_HARNESS


def test_unusable_output_relaunches_and_usable_output_is_judged() -> None:
    exited = ElicitationExit(_record(), _FRESH, alive=False)
    assert exited.decide(usage_limited=False, output_present=True, usable=False) is CollectOutcome.RELAUNCH
    assert exited.decide(usage_limited=False, output_present=True, usable=True) is CollectOutcome.JUDGE


def test_lost_relaunch_spends_no_retry_until_bound() -> None:
    """However many times it was relaunched, a lost elicitation relaunches until the bound
    from its first launch passes — then it fails."""
    for count in (0, 1, 7):
        under = ElicitationExit(_record(count), _FRESH, alive=False)
        assert under.decide(usage_limited=False, output_present=False) is CollectOutcome.RELAUNCH
        past = ElicitationExit(_record(count), _STALE, alive=False)
        assert past.decide(usage_limited=False, output_present=False) is CollectOutcome.FAIL_STALE


# --- verdict ------------------------------------------------------------------------------


def test_verdictless_with_ask_parks() -> None:
    verdict = Verdict.of(None, _CHOICES)
    assert verdict.selected is None
    assert verdict.without_choice(_ASK) is VerdictOutcome.PARK_ON_ASK


def test_verdictless_without_ask_fails() -> None:
    assert Verdict.of(None, _CHOICES).without_choice(None) is VerdictOutcome.FAIL_VERDICTLESS


def test_unknown_choice_with_ask_parks() -> None:
    verdict = Verdict.of("ship-it", _CHOICES)
    assert (verdict.choice, verdict.selected) == ("ship-it", None)
    assert verdict.without_choice(_ASK) is VerdictOutcome.PARK_ON_ASK


def test_unknown_choice_fails() -> None:
    assert Verdict.of("ship-it", _CHOICES).without_choice(None) is VerdictOutcome.FAIL_VERDICTLESS


def test_requires_checks_choice_red_fails() -> None:
    assert Verdict.of("pass", _CHOICES).gated([_GREEN, _RED]) is VerdictOutcome.FAIL_RED_CHECKS


def test_requires_checks_choice_green_accepts() -> None:
    assert Verdict.of("pass", _CHOICES).gated([_GREEN]) is VerdictOutcome.ACCEPT


def test_an_ungated_choice_accepts_over_a_red_check() -> None:
    verdict = Verdict.of("fail", _CHOICES)
    assert verdict.selected == _CHOICES[1]
    assert verdict.gated([_RED]) is VerdictOutcome.ACCEPT


def test_a_requires_checks_choice_with_no_checks_accepts() -> None:
    assert Verdict.of("pass", _CHOICES).gated([]) is VerdictOutcome.ACCEPT


# --- check plan ---------------------------------------------------------------------------


def test_check_plan_empty_without_checks() -> None:
    node = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")]).node
    assert CheckPlan.of(node, "/ws/e1") == CheckPlan(commands=(), cwd="/ws/e1", timeout=DEFAULT_CHECK_TIMEOUT)


def test_check_plan_joins_cwd_defaults_timeout() -> None:
    node = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=[("pass", "ok")], checks=["mise run lint"], checks_cwd="svc"
    ).node
    assert CheckPlan.of(node, "/ws/e1") == CheckPlan(
        commands=("mise run lint",), cwd="/ws/e1/svc", timeout=DEFAULT_CHECK_TIMEOUT
    )


def test_check_plan_keeps_an_authored_timeout_and_order() -> None:
    node = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=[("pass", "ok")], checks=["a", "b"], checks_timeout=30
    ).node
    assert CheckPlan.of(node, "/ws/e1") == CheckPlan(commands=("a", "b"), cwd="/ws/e1", timeout=30)
