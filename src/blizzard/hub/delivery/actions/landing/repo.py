"""One repository's slice of a ``land`` pass, and where it stands once its policy has stepped it.

:class:`RepoLanding` is what a policy is handed: the repository's resolved record — carrying the policy and landing
position its visit took at its first turn — its submitted commit, its :class:`RepositoryForge`, and the pass's
:class:`LandingRun`, through which every marker and landing is handed to the pass's effects. :data:`RepoState` is the
one answer a policy gives about it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.actions.effects import (
    ActionEscalation,
    EffectStatus,
    ForgeAuthReason,
    IEffectSink,
    LandingRecord,
    Marker,
    Pass,
)
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.config.repositories import RepositoryId, ResolvedRepository
from blizzard.hub.domain.kernel.forge_refusals import ForgeDiagnostic, refusal_remedy, refusal_sentence
from blizzard.hub.forge.landing import (
    OPERATION_CAPABILITY,
    Comparison,
    ComparisonStatus,
    ForgeAuthRefused,
    ForgeNotFound,
    ForgeObject,
    ForgeRateLimited,
    ForgeRejected,
    ForgeUnavailable,
    RepositoryForge,
)

#: The answer a pass gives while anything it waits on is unsettled.
PENDING = "pending"

_MARKER_PREFIX = "merged/"


class NotRecorded(Exception):
    """A marker the pass needs durable was not recorded: the chunk moved past the visit."""


class LandingFenced(Exception):
    """A merge's landing record was written past the fence: the chunk moved on while it landed."""


class ForgeEscalation(Exception):
    """A forge answer no worker can repair — a rejected credential, a repository the forge does not know."""

    def __init__(self, escalation: ActionEscalation) -> None:
        super().__init__(escalation.detail)
        self.escalation = escalation


class LandedRevisionUnknown(Exception):
    """A landing is confirmed but the revision it landed at cannot be read — no ``merged/<repo>`` marker may
    be written for it, since a guessed revision is worse than none."""


@dataclass
class LandingRun:
    """One pass's channels out: its sink, and the log lines its ``hub-log.land`` artifact carries."""

    visit: Visit
    effects: IEffectSink
    log: list[str] = field(default_factory=list[str])
    #: The latest moment a rate-limited forge said to come back, read by a ``pending`` answer.
    resume_at: datetime | None = None

    def say(self, line: str) -> None:
        self.log.append(line)

    def hold(self, exc: ForgeUnavailable) -> None:
        """Note a forge that did not answer; a rate limit postpones this pass's next poll to when it lifts."""
        if isinstance(exc, ForgeRateLimited) and (self.resume_at is None or exc.resume_at > self.resume_at):
            self.resume_at = exc.resume_at

    def answer(self, choice: str) -> Pass:
        return Pass(
            choice=choice,
            log=tuple(self.log),
            next_poll_at=self.resume_at if choice in {PENDING, "awaiting-merge"} else None,
        )

    def escalate(self, escalation: ActionEscalation) -> Pass:
        return Pass(choice=None, log=tuple(self.log), escalation=escalation)

    async def post(self, name: str, content: str) -> None:
        """Record marker ``name``, or raise :class:`NotRecorded`."""
        result = await self.effects.record(Marker(name, content))
        if result.status is not EffectStatus.RECORDED:
            raise NotRecorded(f"marker {name!r} was not recorded: {result.detail}")

    async def landed(self, repo: RepoLanding, revision: str) -> None:
        """Hand over ``repo``'s landing at ``revision`` and wait for it; raise :class:`LandingFenced` when it
        was written past the fence."""
        result = await self.effects.record(
            LandingRecord(repository=repo.record.record, repo=repo.repo, revision=revision)
        )
        if result.status is EffectStatus.FENCED:
            raise LandingFenced(f"{repo.repo} landed at {revision}, but {result.detail}")

    async def settle(self, repo: RepoLanding, landed: Landed) -> None:
        """Record ``landed``, then run its follow-up; only the landing itself can raise :class:`LandingFenced`."""
        await self.landed(repo, landed.revision)
        if landed.after is None:
            return
        try:
            await landed.after()
        except (NotRecorded, ForgeEscalation, ForgeRejected, ForgeNotFound, ForgeUnavailable) as exc:
            self.say(f"{repo.repo}: a note after its landing was not written (non-fatal): {exc}")

    def item_lines(self) -> list[str]:
        """``Refs <reference>`` per work item, else its label — never a closing keyword or title."""
        return [
            f"Refs {item['reference']}" if item.get("reference") else str(item["label"])
            for item in self.visit.work_items
        ]


@dataclass(frozen=True)
class RepoLanding:
    """One commit to land and the repository it lands in: its ``owner/repo``, its branch and submitted
    commit, its resolved record — with the terms its visit took — and its forge's roles."""

    run: LandingRun
    repo: str
    branch: str
    commit: str
    record: ResolvedRepository
    forge: RepositoryForge
    #: How a refusal names the forge: its slug and API origin.
    forge_label: str

    @property
    def base_branch(self) -> str:
        return self.record.base_branch

    def marker(self) -> str:
        return f"{_MARKER_PREFIX}{self.repo}"

    async def ask[T](self, call: Awaitable[T]) -> T:
        """``call``'s answer; a rejected credential or a repository the forge does not know raises
        :class:`ForgeEscalation`, since no worker can repair either."""
        where = f"{self.repo} on forge {self.forge_label}"
        try:
            return await call
        except ForgeAuthRefused as exc:
            raise ForgeEscalation(self._auth_refused(exc)) from exc
        except ForgeRejected as exc:
            if exc.reason != ForgeRejected.AUTH:
                raise
            detail = f"{where}: the forge rejected the repository's credential ({exc})"
            raise ForgeEscalation(ActionEscalation(EscalationCause.FORGE_AUTH, detail)) from exc
        except ForgeNotFound as exc:
            if exc.what is not ForgeObject.REPOSITORY:
                raise
            detail = (
                f"{where}: the forge does not know the repository — it does not exist, or cannot be seen with "
                f"this credential ({exc})"
            )
            raise ForgeEscalation(ActionEscalation(EscalationCause.FORGE_CONFIG, detail)) from exc

    def _auth_refused(self, exc: ForgeAuthRefused) -> ActionEscalation:
        """The ``forge-auth`` escalation of a credential the forge refused: what was refused and why, the forge's own
        sanitized words, the credential or secret the call carried, and what to do about it."""
        diagnostic = exc.diagnostic
        needed = OPERATION_CAPABILITY.get(diagnostic.operation)
        capability = str(needed) if needed is not None else None
        detail = "\n".join(
            (
                f"{self.repo} on forge {self.forge_label}: {_operation_sentence(diagnostic)} was refused: "
                f"{refusal_sentence(exc.refusal)}.",
                f"GitHub said: {_forge_said(diagnostic)}.",
                f"{self._carried(diagnostic)}. {refusal_remedy(exc.refusal, diagnostic, capability=capability)}",
            )
        )
        reason = ForgeAuthReason(
            refusal=exc.refusal,
            diagnostic=diagnostic,
            repository=RepositoryId(self.record.record),
            forge=self.record.forge_id,
        )
        return ActionEscalation(EscalationCause.FORGE_AUTH, detail, auth=reason)

    def _carried(self, diagnostic: ForgeDiagnostic) -> str:
        """The credential the refused call carried, as its binding and variant; the secret when it names none."""
        if diagnostic.credential is None:
            return f"Secret {self.record.secret_name}"
        return f"Credential {diagnostic.credential} ({diagnostic.binding}, {diagnostic.variant})"

    async def compare(self, base: str, head: str) -> Comparison | None:
        """The forge's ``base...head`` comparison; ``None`` when the forge refused it or does not know a
        revision."""
        try:
            return await self.ask(self.forge.refs.compare(base, head))
        except (ForgeRejected, ForgeNotFound):
            return None

    async def base_holds(self, ref: str) -> bool:
        """Whether the base branch already holds every commit on ``ref``; a comparison the forge refused is
        ``False`` — never "there was nothing to land"."""
        comparison = await self.compare(self.base_branch, ref)
        return comparison is not None and comparison.status in {ComparisonStatus.IDENTICAL, ComparisonStatus.BEHIND}

    async def base_tip(self) -> str:
        """The base branch's live tip; any unreadable answer raises :class:`LandedRevisionUnknown`."""
        where = f"{self.repo}:{self.base_branch}"
        try:
            tip = await self.ask(self.forge.refs.branch_tip(self.base_branch))
        except ForgeRejected as exc:
            raise LandedRevisionUnknown(f"could not read {where}'s live tip: {exc}") from exc
        if tip is None:
            raise LandedRevisionUnknown(f"{where} does not exist, so it has no live tip")
        return tip


def _operation_sentence(diagnostic: ForgeDiagnostic) -> str:
    return f"the `{diagnostic.operation}` call ({diagnostic.endpoint})"


def _forge_said(diagnostic: ForgeDiagnostic) -> str:
    """The forge's quoted message and the answer's status and request id; a request id it did not send is left
    out."""
    answer = f"HTTP {diagnostic.status}"
    if diagnostic.request_id is not None:
        answer += f", request {diagnostic.request_id}"
    return f'"{diagnostic.message}" ({answer})'


# --- where one repository stands ------------------------------------------------------------------------------------


@domain_model
@dataclass(frozen=True)
class Landed:
    """The base holds the repository's work; ``merged/<repo>`` records ``revision``."""

    revision: str
    #: Best-effort annotations run only after the landing is durable; a refusal of one never undoes the landing.
    after: Callable[[], Awaitable[None]] | None = None


@domain_model
@dataclass(frozen=True)
class NoCode:
    """The declaration needs no merge, recorded without a landing revision."""


@domain_model
@dataclass(frozen=True)
class Waiting:
    """Something unsettled — checks running, a forge still deciding, an update in flight. ``findings`` is the
    record a substantive wait hands on, ``None`` when the wait says nothing worth writing."""

    reason: str
    findings: dict[str, Any] | None = None


@domain_model
@dataclass(frozen=True)
class Ready:
    """Prepared for the ordered landing callback, which may land, wait, request a person, or find no code."""

    land: Callable[[], Awaitable[Landed | Waiting | NeedsPerson | AwaitingMerge | NoCode]]


@domain_model
@dataclass(frozen=True)
class NeedsFreshReview:
    """The previous change closed before this visit and needs a new review."""


@domain_model
@dataclass(frozen=True)
class AwaitingMerge:
    """A parked change, open, waiting on a person."""

    change: int


@domain_model
@dataclass(frozen=True)
class Conflict:
    """The base's tip cannot be brought in mechanically."""

    detail: str


@domain_model
@dataclass(frozen=True)
class Failure:
    """A check the repository's own work fails, or — ``foreign_head`` — a head carrying more than the submitted
    commit and base merges."""

    findings: tuple[dict[str, Any], ...]
    foreign_head: bool = False


@domain_model
@dataclass(frozen=True)
class Inherited:
    """Only checks the base also fails, still red after their one re-run."""

    findings: tuple[dict[str, Any], ...]


@domain_model
@dataclass(frozen=True)
class NeedsPerson:
    """Something no agent can fix; ``escalation`` names its cause and what it is about."""

    escalation: ActionEscalation


type RepoState = Landed | NoCode | Ready | Waiting | AwaitingMerge | Conflict | Failure | Inherited | NeedsPerson


@domain_model
@dataclass(frozen=True)
class Admitted:
    """A repository whose head its policy has proven, the rest of its step held back until every repository's head
    is: ``proceed`` finishes the step. A head refused anywhere in the chunk fires nothing in any other repository,
    so a pass that refuses one never calls ``proceed``."""

    proceed: Callable[[], Awaitable[RepoState]]
