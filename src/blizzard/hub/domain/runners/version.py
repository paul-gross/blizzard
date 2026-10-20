"""The version handshake — the hub's compatibility window judging each runner registration's reported version."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.foundation.runner_version import VersionRefusalReason


@domain_model
@dataclass(frozen=True)
class VersionRefusal:
    """Why the window refuses a version, and the operator-facing sentence naming what to upgrade."""

    reason: VersionRefusalReason
    detail: str


@domain_model
@dataclass(frozen=True)
class VersionJudgement:
    """The window's judgement of one reported version, with the window it was judged against."""

    #: The version as reported; ``None`` when absent or unparseable.
    reported_version: str | None
    #: The window's refusal; ``None`` when admitted.
    refusal: VersionRefusal | None
    hub_version: str
    floor: str
    #: The newest line the hub admits — its own.
    ceiling: str


class IVersionWindow(Protocol):
    """The hub's version and window, judging a runner's reported version."""

    def judge(self, version: str | None) -> VersionJudgement:
        """Judge a runner reporting ``version`` (``None`` when it reports none)."""
        ...


class RunnerVersionRefused(Exception):
    """A registration whose version the window refuses — recorded on the runner's row before this is raised.
    ``changed`` is whether the recorded judgement differs from the one the row held."""

    def __init__(self, runner_id: str, judgement: VersionJudgement, refusal: VersionRefusal, *, changed: bool) -> None:
        super().__init__(refusal.detail)
        self.runner_id = runner_id
        self.judgement = judgement
        self.refusal = refusal
        self.changed = changed
