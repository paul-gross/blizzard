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
    """The window's judgement of one reported version (fields as ``blizzard.wire.version_window.Admission``)."""

    reported_version: str | None
    refusal: VersionRefusal | None
    hub_version: str
    floor: str
    ceiling: str


class IVersionWindow(Protocol):
    """The hub's version and window, judging a runner's reported version."""

    def judge(self, version: str | None) -> VersionJudgement: ...


class RunnerVersionRefused(Exception):
    """A refused registration, recorded on the row first; ``changed`` if the row's judgement changed."""

    def __init__(self, runner_id: str, judgement: VersionJudgement, refusal: VersionRefusal, *, changed: bool) -> None:
        super().__init__(refusal.detail)
        self.runner_id = runner_id
        self.judgement = judgement
        self.refusal = refusal
        self.changed = changed
