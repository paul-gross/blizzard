"""Checking a credential read-only: what each record using it asks of its forge, and what the forge answers.

A check builds the credential's authorization for :attr:`ForgeScope.CHECK`, makes only the reads landing and ingestion
make, and judges every write from the grant the credential exposes, never by exercising it. It stores nothing but the
one ``credential-check`` event it records. The forge kind's :class:`IReadinessProbe` makes the calls; this module owns
the outcomes and which uses are checked, and imports no HTTP library."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

from blizzard.foundation.clock import IClock, IMonotonicClock
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.changes import RecordKind, RecordRef
from blizzard.hub.domain.config.credentials import (
    ConfiguredCredential,
    IReadCredentialRepository,
    resolved_credential,
)
from blizzard.hub.domain.config.forges import IReadForgeRepository
from blizzard.hub.domain.config.landing_policies import POLICY_CAPABILITIES
from blizzard.hub.domain.config.repositories import ConfiguredRepository, IReadRepositoryRecordRepository
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource, IReadWorkSourceRepository
from blizzard.hub.domain.kernel.forge_refusals import (
    ClassifiedRefusal,
    ForgeAccessUnavailable,
    ForgeDiagnostic,
    ForgeMintRefused,
    refusal_remedy,
    refusal_sentence,
)
from blizzard.hub.forge.auth import ForgeAuthorization, ForgeScope, IForgeAuthorizations
from blizzard.hub.forge.budget import ForgeBudgets, SweepingGate

#: What every check says it did not judge.
NOT_EVALUATED = "Branch protection, required reviews, and rulesets are not evaluated."


class CheckOutcome(StrEnum):
    """What a check found of one operation."""

    #: A read landing or ingestion makes answered 2xx.
    PROVEN = "proven"
    #: A write the credential holds a readable grant for; not exercised.
    GRANTED = "granted"
    #: A write no readable grant covers — a fine-grained token exposes none; not exercised.
    UNPROVEN = "unproven"
    #: The forge refused the call; its diagnostic says why.
    REFUSED = "refused"
    #: Outside what the credential's authorization offers; no call made.
    UNSUPPORTED = "unsupported"


#: The operation a repository use's reachability is read by.
ACCESS = "access"
#: The operation an App credential's installation token is minted by.
MINT = "mint"

type UseKind = Literal["repository", "work_source"]


@domain_model
@dataclass(frozen=True)
class OperationResult:
    """One operation's outcome: ``operation`` is ``access``, ``mint``, or what a use asks of its forge."""

    operation: str
    outcome: CheckOutcome
    detail: str | None = None
    diagnostic: ForgeDiagnostic | None = None


@domain_model
@dataclass(frozen=True)
class UseResult:
    """One use's operations; ``reached`` is ``False`` when the check ended before this use was wholly answered."""

    kind: UseKind
    id: str
    slug: str
    operations: tuple[OperationResult, ...]
    reached: bool


@domain_model
@dataclass(frozen=True)
class CredentialCheck:
    """A credential's check: who it is, ``mint`` for a credential minting its token, the grant it exposes, and each
    use's result."""

    credential: str
    binding: str
    variant: str | None
    variant_contradicted: bool
    mint: OperationResult | None
    granted_permissions: Mapping[str, str] | None
    uses: tuple[UseResult, ...]
    not_evaluated: str = NOT_EVALUATED

    def counts(self) -> dict[str, int]:
        """How many operations, the mint among them, ended in each outcome."""
        tally = Counter(
            operation.outcome
            for operation in (
                *([self.mint] if self.mint is not None else []),
                *(operation for use in self.uses for operation in use.operations),
            )
        )
        return {outcome.value: tally[outcome] for outcome in CheckOutcome}


@domain_model
@dataclass(frozen=True)
class CheckedUse:
    """What one use asks of its forge: its record, its ``owner/repo``, the base branch of a repository, and the
    capabilities its landing policy requires — empty for a work source, whose operations are its kind's."""

    kind: UseKind
    id: str
    slug: str
    locator: str
    base_branch: str | None
    capabilities: frozenset[Capability]

    def unreached(self, operations: tuple[OperationResult, ...] = ()) -> UseResult:
        """This use, ended before it was wholly answered."""
        return UseResult(self.kind, self.id, self.slug, operations, reached=False)

    def answered(self, operations: tuple[OperationResult, ...]) -> UseResult:
        return UseResult(self.kind, self.id, self.slug, operations, reached=True)


@domain_model
@dataclass(frozen=True)
class CheckBudget:
    """How long one call, and the whole check, may take, in seconds of real time."""

    per_call_seconds: float = 30.0
    per_check_seconds: float = 60.0


class CheckDeadline:
    """One check's deadline on an elapsed-time clock — never the stack's wall clock, so a controlled clock's check
    still stops in real time."""

    def __init__(self, clock: IMonotonicClock, budget: CheckBudget) -> None:
        self._clock = clock
        self._budget = budget
        self._ends = clock.monotonic() + budget.per_check_seconds

    def remaining(self) -> float:
        return max(0.0, self._ends - self._clock.monotonic())

    def spent(self) -> bool:
        return self.remaining() <= 0.0

    def call_timeout(self) -> float:
        """The timeout of the next call: the per-call bound, or what remains of the check when less."""
        return min(self._budget.per_call_seconds, self.remaining())


@domain_model
@dataclass(frozen=True)
class ProbeAnswer:
    """What a probe found: the mint, for a credential minting its token; the grant; and each use's result."""

    mint: OperationResult | None
    granted_permissions: Mapping[str, str] | None
    uses: tuple[UseResult, ...]


class IReadinessProbe(Protocol):
    """One forge kind's read-only check of an authorization against the uses it serves."""

    def check(
        self,
        authorization: ForgeAuthorization,
        uses: Sequence[CheckedUse],
        *,
        api_base: str,
        gate: SweepingGate,
        deadline: CheckDeadline,
    ) -> ProbeAnswer:
        """Each use's result, in ``uses``' order, through ``gate`` and within ``deadline``."""
        ...


def refused_mint(classified: ClassifiedRefusal) -> OperationResult:
    """A refused mint or installation read, its detail the refusal and what to do about it."""
    return OperationResult(MINT, CheckOutcome.REFUSED, refusal_detail(classified), classified.diagnostic)


def refusal_detail(classified: ClassifiedRefusal, *, capability: str | None = None) -> str:
    """The refusal as an operator reads it: the sentence, then the remedy."""
    sentence = refusal_sentence(classified.refusal)
    remedy = refusal_remedy(classified.refusal, classified.diagnostic, capability=capability)
    return f"{sentence[0].upper()}{sentence[1:]}. {remedy}"


def mint_refused_answer(
    classified: ClassifiedRefusal, uses: Sequence[CheckedUse], granted: Mapping[str, str] | None
) -> ProbeAnswer:
    """A check whose mint the forge refused: it went as far as it can, so every use is reached with no operations."""
    return ProbeAnswer(refused_mint(classified), granted, tuple(use.answered(()) for use in uses))


class CredentialNotUsedBy(Exception):
    """A check named a repository or work source that does not act with the credential; ``uses`` names each."""

    code = "credential_not_used_by"

    def __init__(self, credential: str, uses: tuple[RecordRef, ...]) -> None:
        super().__init__(f"credential {credential} is not used by {', '.join(str(use) for use in uses)}")
        self.credential = credential
        self.uses = uses


class CredentialReadiness:
    """One tenant's credential check: resolves the credential and the uses it checks, builds its authorization for
    :attr:`ForgeScope.CHECK`, runs its forge kind's probe at the forge key's sweeping priority, and records one
    ``credential-check`` event. It writes nothing else."""

    def __init__(
        self,
        *,
        repositories: IReadRepositoryRecordRepository,
        work_sources: IReadWorkSourceRepository,
        credentials: IReadCredentialRepository,
        forges: IReadForgeRepository,
        authorizations: IForgeAuthorizations,
        budgets: ForgeBudgets,
        probes: Mapping[str, IReadinessProbe],
        event_log: EventLogService,
        clock: IClock,
        monotonic: IMonotonicClock,
        budget: CheckBudget | None = None,
    ) -> None:
        self._repositories = repositories
        self._work_sources = work_sources
        self._credentials = credentials
        self._forges = forges
        self._authorizations = authorizations
        self._budgets = budgets
        self._probes = probes
        self._event_log = event_log
        self._clock = clock
        self._monotonic = monotonic
        self._budget = budget or CheckBudget()

    def check(
        self,
        credential: ConfiguredCredential,
        *,
        repositories: Sequence[str] | None = None,
        work_sources: Sequence[str] | None = None,
    ) -> CredentialCheck:
        """Check ``credential`` against the repositories and work sources named by id or slug, else against every
        enabled record acting with it. :class:`CredentialNotUsedBy` when a named record does not act with it;
        :class:`~blizzard.hub.domain.config.credentials.CredentialUnavailable` when it or its secret is retired or
        its secret unknown."""
        uses = self._uses(credential, repositories, work_sources)
        resolved = resolved_credential(credential)
        forge = self._forges.get(credential.fields.forge_id)
        # A credential always names a forge, and a configured record is never deleted.
        assert forge is not None
        deadline = CheckDeadline(self._monotonic, self._budget)
        unavailable: str | None = None
        try:
            authorization = self._authorizations.authorization(resolved, ForgeScope.CHECK)
        except ForgeMintRefused as exc:
            answer = mint_refused_answer(exc.classified, uses, None)
            check = self._checked(credential, None, answer)
        except ForgeAccessUnavailable as exc:
            unavailable = str(exc)
            answer = ProbeAnswer(None, None, tuple(use.unreached() for use in uses))
            check = self._checked(credential, None, answer)
        else:
            gate = self._budgets.sweeping(forge.forge_id, authorization.authority, forge.kind)
            answer = self._probes[forge.kind].check(
                authorization, uses, api_base=forge.fields.api_base, gate=gate, deadline=deadline
            )
            check = self._checked(credential, authorization, answer)
        self._record(credential, check, unavailable)
        return check

    def _uses(
        self,
        credential: ConfiguredCredential,
        repositories: Sequence[str] | None,
        work_sources: Sequence[str] | None,
    ) -> tuple[CheckedUse, ...]:
        if not repositories and not work_sources:
            enabled_repositories = self._repositories.naming_credential(credential.credential_id)
            enabled_sources = self._work_sources.naming_credential(credential.credential_id)
            return (
                *(_repository_use(record) for record in enabled_repositories if not record.retired),
                *(_work_source_use(record) for record in enabled_sources if not record.retired),
            )
        refused: list[RecordRef] = []
        uses: list[CheckedUse] = []
        for value in repositories or ():
            repository = self._repositories.resolve(value)
            if repository is None:
                refused.append(RecordRef(RecordKind.REPOSITORY, value))
            elif repository.fields.credential_id != credential.credential_id:
                refused.append(RecordRef(RecordKind.REPOSITORY, repository.repository_id, repository.slug))
            else:
                uses.append(_repository_use(repository))
        for value in work_sources or ():
            source = self._work_sources.resolve(value)
            if source is None:
                refused.append(RecordRef(RecordKind.WORK_SOURCE, value))
            elif source.fields.credential_id != credential.credential_id:
                refused.append(RecordRef(RecordKind.WORK_SOURCE, source.work_source_id, source.slug))
            else:
                uses.append(_work_source_use(source))
        if refused:
            raise CredentialNotUsedBy(credential.slug, tuple(refused))
        return tuple(uses)

    @staticmethod
    def _checked(
        credential: ConfiguredCredential, authorization: ForgeAuthorization | None, answer: ProbeAnswer
    ) -> CredentialCheck:
        return CredentialCheck(
            credential=credential.slug,
            binding=credential.fields.binding,
            variant=authorization.variant if authorization is not None else None,
            variant_contradicted=authorization.variant_contradicted if authorization is not None else False,
            mint=answer.mint,
            granted_permissions=answer.granted_permissions,
            uses=answer.uses,
        )

    def _record(self, credential: ConfiguredCredential, check: CredentialCheck, unavailable: str | None) -> None:
        counts = check.counts()
        said = ", ".join(f"{count} {outcome}" for outcome, count in counts.items())
        unreached = sum(1 for use in check.uses if not use.reached)
        message = f"credential {credential.slug} checked: {said}"
        if unreached:
            message += f"; {unreached} of {len(check.uses)} uses not reached"
        if unavailable is not None:
            message += f" ({unavailable})"
        self._event_log.record(
            kind="credential-check",
            runner_id=None,
            chunk_id=None,
            project_id=None,
            lease_id=None,
            node_name=None,
            message=message,
            detail={"credential_id": credential.credential_id, "counts": counts},
            at=self._clock.now(),
        )


def _repository_use(record: ConfiguredRepository) -> CheckedUse:
    return CheckedUse(
        kind="repository",
        id=record.repository_id,
        slug=record.slug,
        locator=f"{record.fields.owner}/{record.fields.repo}",
        base_branch=record.fields.base_branch,
        capabilities=POLICY_CAPABILITIES[record.fields.landing_policy].requires,
    )


def _work_source_use(record: ConfiguredWorkSource) -> CheckedUse:
    return CheckedUse(
        kind="work_source",
        id=record.work_source_id,
        slug=record.slug,
        locator=record.fields.locator,
        base_branch=None,
        capabilities=frozenset(),
    )
