"""The aggregate ``blizzard runner status`` reads, shared by its text and its ``--json`` rendering."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.leases import LeaseLiveness
from blizzard.wire.decision import DecisionView, OpenDecisionsResponse
from blizzard.wire.lease import LeaseListResponse, LeaseView
from blizzard.wire.runner_host import (
    HostEnvironmentListResponse,
    HostEnvironmentView,
    HostSubscriptionListResponse,
    HostSubscriptionView,
)
from blizzard.wire.runner_status import (
    AskListResponse,
    AskView,
    EscalationListResponse,
    EscalationView,
    HarnessHealthListResponse,
    HarnessHealthView,
    OpenTakeoverListResponse,
    OpenTakeoverView,
    RunnerStatusView,
    UnmatchedBindingView,
)
from blizzard.wire.runner_traces import RunnerTraceStatusResponse


class NotRequested(BaseModel):
    """A section the view did not read, because nothing in it called for it."""

    state: Literal["not-requested"]


class Ok[T](BaseModel):
    """A section its read answered."""

    state: Literal["ok"]
    answer: T


class Unavailable(BaseModel):
    """A section whose read the daemon answered with a non-2xx ``status``."""

    state: Literal["unavailable"]
    status: int


class RunnerStatus(BaseModel):
    """What ``status`` shows: each required section always present, each conditional or optional section in one of
    its states. Built once by ``assemble_status`` and consumed whole by both renderers, so they cannot select
    differently."""

    model_config = ConfigDict(frozen=True)

    runner: RunnerStatusView
    leases: list[LeaseView]
    held_environments: list[HostEnvironmentView]
    unmatched_bindings: list[UnmatchedBindingView]
    open_asks: list[AskView]
    escalations: list[EscalationView]
    open_takeovers: list[OpenTakeoverView]
    subscriptions: list[HostSubscriptionView]
    harness_health: NotRequested | Ok[list[HarnessHealthView]]
    traces: Ok[RunnerTraceStatusResponse] | Unavailable
    gate_decisions: Ok[list[DecisionView]] | Unavailable


def harness_health_items(status: RunnerStatus) -> list[HarnessHealthView]:
    """The harnesses' health the view read, or none where it did not read it."""
    return status.harness_health.answer if isinstance(status.harness_health, Ok) else []


def needs_harness_health(escalations: EscalationListResponse) -> bool:
    """Whether an open escalation was parked for want of an acceptable harness, so the harnesses' health explains it."""
    return any(esc.cause == EscalationCause.NO_ACCEPTABLE_HARNESS for esc in escalations.items)


def assemble_status(
    *,
    runner_id: str,
    view: RunnerStatusView,
    leases: LeaseListResponse,
    environments: HostEnvironmentListResponse,
    asks: AskListResponse,
    escalations: EscalationListResponse,
    takeovers: OpenTakeoverListResponse,
    subscriptions: HostSubscriptionListResponse,
    harness_health: HarnessHealthListResponse | None,
    traces: RunnerTraceStatusResponse | int,
    decisions: OpenDecisionsResponse | int,
) -> RunnerStatus:
    """The aggregate of the decoded reads, keeping what the text lists: the leases not ended, the environments this
    runner holds, the held bindings matching no workspace, and the subscriptions this runner spends. An optional read
    is its answer, or the non-2xx status it drew."""
    return RunnerStatus(
        runner=view,
        leases=[lease for lease in leases.items if lease.liveness != LeaseLiveness.ENDED],
        held_environments=[
            env
            for workspace in environments.workspaces
            for env in workspace.items
            if env.held and env.runner_id == runner_id
        ],
        unmatched_bindings=view.unmatched_bindings,
        open_asks=asks.items,
        escalations=escalations.items,
        open_takeovers=takeovers.items,
        subscriptions=[sub for sub in subscriptions.items if runner_id in sub.spenders],
        harness_health=(
            NotRequested(state="not-requested")
            if harness_health is None
            else Ok[list[HarnessHealthView]](state="ok", answer=harness_health.items)
        ),
        traces=(
            Unavailable(state="unavailable", status=traces)
            if isinstance(traces, int)
            else Ok[RunnerTraceStatusResponse](state="ok", answer=traces)
        ),
        gate_decisions=(
            Unavailable(state="unavailable", status=decisions)
            if isinstance(decisions, int)
            else Ok[list[DecisionView]](state="ok", answer=decisions.decisions)
        ),
    )
