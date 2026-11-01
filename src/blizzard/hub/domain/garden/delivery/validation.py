"""Delivery validation — the hub-executed delivery node's shape
check, before anything is written; the check itself is specified by
blizzard-product:/delivered/garden/machinery.md §Delivery. Pure functions over already-loaded
objects (`bzh:domain-takes-objects`), no I/O. Materializing a passing result is
`src/blizzard/hub/domain/garden/delivery/materialize.py`'s."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.garden.findings.bucket import FindingBucket
from blizzard.hub.domain.garden.formats import (
    DeliveredDelta,
    FindingAddOp,
    FindingGoneOp,
    IGardenFormats,
    ProposalCandidate,
)
from blizzard.hub.domain.garden.proposals.model import GardenProposalBlankFieldError, require_text
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.kernel.garden_delivery_fault import GardenDeliveryFault

# Lowercase hex, 7-40 characters — a well-formed commit sha's shape. Says nothing about
# whether the commit actually exists; that is `CommitResolver`'s question.
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")


class GardenDeliveryRejected(Exception):
    """A delivered artifact failed validation. This exception's own message is the
    operator-legible reason the graph attaches to its bounce — never a raw pydantic
    error, never a stack of causes a person has to untangle. ``fault`` names the artifact
    to repair; a citation that spans both is the docket's, which carries it."""

    def __init__(self, message: str, fault: GardenDeliveryFault) -> None:
        super().__init__(message)
        self.fault = fault


# Every finding known to this routine, live or gone, minus an exited one:
# keyed by finding id, valued by that finding's own recorded scope slug.
LiveFindings = Mapping[str, str]


# Which of the given ids are exited findings this run's bucket would otherwise hold: one bulk read
# over only the ids a delivery cites, so exited history is never read whole.
ExitedResolver = Callable[[frozenset[str]], frozenset[str]]


@domain_model
@dataclass(frozen=True)
class CommitResolution:
    exists: bool
    authored_at: datetime | None = None


# Resolves `(repo, commit_sha)`: a :class:`CommitResolution` when `repo` is addressable,
# `None` when it is not, degrading that commit to a well-formedness check.
CommitResolver = Callable[[str, str], CommitResolution | None]

# What resolving the commits a delivery cites produced, keyed `(repo, sha)`: a key absent was never asked,
# a `None` value is a repository the resolver found unaddressable.
CommitResolutions = Mapping[tuple[str, str], CommitResolution | None]


@domain_model
@dataclass(frozen=True)
class ParsedArtifacts:
    """A delivery's named artifacts after `IGardenFormats` parsed them: the deltas, the proposal candidates
    of every proposal artifact flattened, each candidate's own artifact name positionally, and the
    candidates grouped per artifact for the per-artifact ref check."""

    deltas: list[DeliveredDelta]
    proposals: list[ProposalCandidate]
    proposal_sources: list[str]
    candidates_by_artifact: dict[str, list[ProposalCandidate]]


@domain_model
@dataclass(frozen=True)
class ValidatedDelivery:
    """What a passing :func:`validate_delivery` hands the next phase: the run it was
    delivered under and the parsed, checked deltas and proposal candidates to build a
    `DeliveryPlan` from — nothing here is durable yet (machinery.md §Delivery)."""

    run: RunContext
    deltas: list[DeliveredDelta]
    proposals: list[ProposalCandidate]
    proposal_sources: list[str] = field(default_factory=list[str])  # proposals[i]'s own artifact name, positionally
    #: `(repo, sha)`'s resolved authored instant — `None` when unresolved, absent when never attempted.
    introduced_at: dict[tuple[str, str], datetime | None] = field(
        default_factory=dict[tuple[str, str], datetime | None]
    )
    #: Every citable finding's :meth:`Finding.run_gone`: the fact kind and actor a run's `gone` lands as.
    gone_settlements: dict[str, tuple[str, str | None]] = field(default_factory=dict[str, tuple[str, str | None]])
    #: Finding id → the state validation judged it in, for every finding an `observed`/`gone` op
    #: names — what the write re-checks under the finding's row lock.
    expect: dict[str, str] = field(default_factory=dict[str, str])


@domain_model
@dataclass(frozen=True)
class SelectedArtifacts:
    """The named artifacts a delivery reads: artifact name → raw content, and name →
    artifact id, in the order the delivery named them."""

    contents: dict[str, str]
    artifact_ids: dict[str, str]


class NamedArtifact(Protocol):
    """What a delivery reads of one named artifact: its raw content and its id."""

    @property
    def data(self) -> str: ...

    @property
    def artifact_id(self) -> str: ...


def select_delta_artifacts(names: Sequence[str], latest: Mapping[str, NamedArtifact]) -> SelectedArtifacts:
    """The delta artifacts `names` names, out of `latest` (each name's newest artifact).
    Refuses a delivery naming no delta, or naming one with no artifact."""
    missing = [name for name in names if name not in latest]
    if not names or missing:
        raise GardenDeliveryRejected(
            f"delta artifact not found — missing: {', '.join(missing) or '<none named>'}", GardenDeliveryFault.DELTA
        )
    return SelectedArtifacts(
        contents={name: latest[name].data for name in names},
        artifact_ids={name: latest[name].artifact_id for name in names},
    )


def select_proposal_artifacts(names: Sequence[str], latest: Mapping[str, NamedArtifact]) -> SelectedArtifacts:
    """The proposals artifacts `names` names, out of `latest`; a named one with no artifact
    is skipped."""
    present = [name for name in names if name in latest]
    return SelectedArtifacts(
        contents={name: latest[name].data for name in present},
        artifact_ids={name: latest[name].artifact_id for name in present},
    )


def is_finding_id_shaped(value: str) -> bool:
    """Whether `value` parses as a well-formed `fin_<ULID>` id — the shape rule a
    proposal's `findings` entry is discriminated on: true resolves against
    `live_findings`; false resolves against a `ref` an `add` op in this same delivery
    carries. Shared with `src/blizzard/hub/domain/garden/delivery/materialize.py`."""
    parsed = Id.parse(value)
    return parsed is not None and parsed.has_prefix(IdPrefix.FINDING)


def single_repo_of(delta: DeliveredDelta) -> str | None:
    """The one repository `delta.revisions` names, or `None` if it names zero or several
    — `introduced` carries no repository of its own, so this is the sole case its commit
    resolves against. Shared with `src/blizzard/hub/domain/garden/delivery/materialize.py` so the
    rule is asked once, not reimplemented and left to silently diverge."""
    return next(iter(delta.revisions)) if len(delta.revisions) == 1 else None


def check_delta(
    delta: DeliveredDelta,
    *,
    run: RunContext,
    live_findings: LiveFindings,
    exited_ids: frozenset[str] = frozenset(),
    resolutions: CommitResolutions | None = None,
) -> dict[tuple[str, str], datetime | None]:
    """Validate one already-parsed delta against `run` and `live_findings`, raising
    :class:`GardenDeliveryRejected` on the first failure: `delta.scope`, every commit sha,
    every `add` op's non-blank class, locus and summary, every transformation's id, one op per id, and a
    `gone` op's non-empty note. `exited_ids`
    only distinguishes an exited id from an unknown one in the message. Returns every
    `(repo, sha)` resolved for an `add`'s `introduced` commit, for `introduced_at`."""
    if delta.scope != run.scope_slug:
        raise GardenDeliveryRejected(
            f"delta declares scope {delta.scope!r}, this run's declared scope is {run.scope_slug!r}",
            GardenDeliveryFault.DELTA,
        )
    for repo, sha in delta.revisions.items():
        _resolved(repo, sha, resolutions)

    introduced_at: dict[tuple[str, str], datetime | None] = {}
    single_repo = single_repo_of(delta)
    seen_ids: set[str] = set()
    for op in delta.findings:
        if isinstance(op, FindingAddOp):
            for value, field_name in ((op.class_, "class"), (op.locus, "locus"), (op.summary, "summary")):
                if not value.strip():
                    raise GardenDeliveryRejected(
                        f"a finding addition has a blank {field_name}", GardenDeliveryFault.DELTA
                    )
            if op.ref is not None and is_finding_id_shaped(op.ref):
                # Otherwise the shape rule a proposal's citation is discriminated on
                # could be gamed into rebinding an existing finding id.
                raise GardenDeliveryRejected(
                    f"add op ref {op.ref!r} is shaped like a finding id and cannot be used as a submission-local ref",
                    GardenDeliveryFault.DELTA,
                )
            if op.introduced is not None:
                # `introduced` names no repository of its own, so it resolves only against
                # a sole declared one; zero or several leave which one ambiguous.
                if single_repo is not None:
                    resolution = _resolved(single_repo, op.introduced, resolutions)
                    introduced_at[(single_repo, op.introduced)] = resolution.authored_at if resolution else None
                else:
                    _check_commit_wellformed(op.introduced, context="a finding addition's introduced commit")
            continue
        _check_known_id(
            op.id,
            run=run,
            live_findings=live_findings,
            scope=delta.scope,
            exited_ids=exited_ids,
            fault=GardenDeliveryFault.DELTA,
        )
        if op.id in seen_ids:
            # Two ops naming one finding could durably write contradictory facts, an
            # `observed` and a `resolved` both landing on the same record.
            raise GardenDeliveryRejected(
                f"finding {op.id!r} is named by more than one op in this delta", GardenDeliveryFault.DELTA
            )
        seen_ids.add(op.id)
        if isinstance(op, FindingGoneOp) and not op.note.strip():
            raise GardenDeliveryRejected(
                f"finding {op.id!r}'s gone fact must carry a non-empty note", GardenDeliveryFault.DELTA
            )
    return introduced_at


def check_add_refs(deltas: Sequence[DeliveredDelta]) -> frozenset[str]:
    """Every `ref` an `add` op carries across `deltas` — what :func:`check_proposal`
    resolves a non-`fin_` citation against — raising :class:`GardenDeliveryRejected` if
    one is carried by more than one op, the same way :func:`check_proposal_refs` already
    refuses a duplicated proposal ref."""
    seen: set[str] = set()
    for delta in deltas:
        for op in delta.findings:
            if isinstance(op, FindingAddOp) and op.ref is not None:
                if op.ref in seen:
                    raise GardenDeliveryRejected(
                        f"add op ref {op.ref!r} is carried more than once in this delivery", GardenDeliveryFault.DELTA
                    )
                seen.add(op.ref)
    return frozenset(seen)


def check_proposal(
    proposal: ProposalCandidate,
    *,
    run: RunContext,
    live_findings: LiveFindings,
    exited_ids: frozenset[str] = frozenset(),
    known_refs: frozenset[str] = frozenset(),
) -> None:
    """Validate one already-parsed proposal candidate: a blank title, class, or body is refused first, then
    each `proposal.findings` entry must be a `fin_<ULID>` live on `run.routine_name` or a `ref` an `add` op
    in this delivery carries (`known_refs`), and none may repeat — the primary key carries a finding once
    per proposal."""
    for value, field_name in ((proposal.title, "title"), (proposal.class_, "class"), (proposal.body, "body")):
        try:
            require_text(value, field_name)
        except GardenProposalBlankFieldError:
            raise GardenDeliveryRejected(
                f"proposal {proposal.ref!r} has a blank {field_name}", GardenDeliveryFault.PROPOSALS
            ) from None
    seen: set[str] = set()
    for entry in proposal.findings:
        if entry in seen:
            raise GardenDeliveryRejected(
                f"proposal {proposal.ref!r} cites {entry!r} more than once", GardenDeliveryFault.PROPOSALS
            )
        seen.add(entry)
        if is_finding_id_shaped(entry):
            _check_known_id(
                entry,
                run=run,
                live_findings=live_findings,
                scope=None,
                exited_ids=exited_ids,
                fault=GardenDeliveryFault.PROPOSALS,
            )
        elif entry not in known_refs:
            raise GardenDeliveryRejected(
                f"proposal cites {entry!r}, which is neither a live finding id nor a ref carried by an add op "
                "in this delivery's own deltas",
                GardenDeliveryFault.PROPOSALS,
            )


def check_proposal_refs(artifact_name: str, proposals: Sequence[ProposalCandidate]) -> None:
    """Validate that one artifact's candidates carry distinct `ref`s. A delivered
    proposal is identified by its source artifact plus its submission-local `ref`, so an
    artifact naming one twice says two different proposals are the same one — rejected
    here as a legible bounce rather than left to collide at insert. Two *different*
    artifacts reusing a `ref` name unrelated proposals and are fine."""
    seen: set[str] = set()
    for proposal in proposals:
        if proposal.ref in seen:
            raise GardenDeliveryRejected(
                f"artifact {artifact_name!r} carries the proposal ref {proposal.ref!r} more than once",
                GardenDeliveryFault.PROPOSALS,
            )
        seen.add(proposal.ref)


def parse_artifacts(
    *, delta_artifacts: Mapping[str, str], proposal_artifacts: Mapping[str, str], formats: IGardenFormats
) -> ParsedArtifacts:
    """`delta_artifacts`/`proposal_artifacts` map each artifact name to its unparsed raw JSON text; `formats`
    parses each, raising :class:`GardenDeliveryRejected` for one it cannot."""
    deltas = [formats.finding_delta(name, raw) for name, raw in delta_artifacts.items()]
    proposals: list[ProposalCandidate] = []
    proposal_sources: list[str] = []
    by_artifact: dict[str, list[ProposalCandidate]] = {}
    for name, raw in proposal_artifacts.items():
        candidates = formats.proposal_candidates(name, raw)
        by_artifact[name] = list(candidates)
        proposals.extend(candidates)
        proposal_sources.extend(name for _ in candidates)
    return ParsedArtifacts(
        deltas=deltas, proposals=proposals, proposal_sources=proposal_sources, candidates_by_artifact=by_artifact
    )


def commits_to_resolve(deltas: Sequence[DeliveredDelta], *, run: RunContext) -> frozenset[tuple[str, str]]:
    """Every `(repo, sha)` :func:`check_delta` would ask a resolver about across `deltas`: each declared
    revision and each `add` op's `introduced` commit under a sole declared repository. A malformed sha is
    skipped (the check rejects it by shape) and so is a delta outside the run's scope (it is rejected before
    any commit is asked about)."""
    asked: set[tuple[str, str]] = set()
    for delta in deltas:
        if delta.scope != run.scope_slug:
            continue
        asked.update((repo, sha) for repo, sha in delta.revisions.items() if _COMMIT_RE.fullmatch(sha))
        single_repo = single_repo_of(delta)
        if single_repo is None:
            continue
        for op in delta.findings:
            if isinstance(op, FindingAddOp) and op.introduced is not None and _COMMIT_RE.fullmatch(op.introduced):
                asked.add((single_repo, op.introduced))
    return frozenset(asked)


def resolve_commits(commits: Iterable[tuple[str, str]], resolver: CommitResolver | None) -> CommitResolutions:
    """Ask `resolver` once about each of `commits`; with no resolver nothing is asked."""
    if resolver is None:
        return {}
    return {(repo, sha): resolver(repo, sha) for repo, sha in sorted(commits)}


def validate_delivery(
    *,
    run: RunContext,
    parsed: ParsedArtifacts,
    bucket: FindingBucket,
    resolutions: CommitResolutions | None = None,
    resolve_exited: ExitedResolver = lambda _ids: frozenset(),
) -> ValidatedDelivery:
    """The delivery node's whole check over `parsed` artifacts and the `resolutions` of the commits they cite
    (:func:`commits_to_resolve`). `bucket` is the run's :class:`FindingBucket`: what it may cite;
    `resolve_exited` is asked once about the cited ids the bucket lacks, only to tell an exited id from an
    unknown one in the message. Raises :class:`GardenDeliveryRejected` on the first failure; on success
    returns a :class:`ValidatedDelivery`, nothing durable."""
    live_findings: LiveFindings = {f.finding_id: f.scope_slug for f in bucket.citable}
    gone_settlements = {f.finding_id: f.run_gone() for f in bucket.citable}
    deltas = parsed.deltas
    proposals = parsed.proposals
    for name, candidates in parsed.candidates_by_artifact.items():
        check_proposal_refs(name, candidates)

    cited = {op.id for delta in deltas for op in delta.findings if not isinstance(op, FindingAddOp)}
    cited.update(entry for proposal in proposals for entry in proposal.findings)
    absent = frozenset(i for i in cited if is_finding_id_shaped(i) and i not in live_findings)
    exited_ids: frozenset[str] = resolve_exited(absent) if absent else frozenset()

    introduced_at: dict[tuple[str, str], datetime | None] = {}
    for delta in deltas:
        introduced_at.update(
            check_delta(
                delta, run=run, live_findings=live_findings, exited_ids=exited_ids, resolutions=resolutions or {}
            )
        )
    known_refs = check_add_refs(deltas)
    for proposal in proposals:
        check_proposal(proposal, run=run, live_findings=live_findings, exited_ids=exited_ids, known_refs=known_refs)

    states = {f.finding_id: f.state for f in bucket.citable}
    expect = {op.id: states[op.id] for delta in deltas for op in delta.findings if not isinstance(op, FindingAddOp)}

    return ValidatedDelivery(
        run=run,
        deltas=deltas,
        proposals=proposals,
        proposal_sources=parsed.proposal_sources,
        introduced_at=introduced_at,
        gone_settlements=gone_settlements,
        expect=expect,
    )


def _check_known_id(
    finding_id: str,
    *,
    run: RunContext,
    live_findings: LiveFindings,
    scope: str | None,
    fault: GardenDeliveryFault,
    exited_ids: frozenset[str] = frozenset(),
) -> None:
    if not is_finding_id_shaped(finding_id):
        raise GardenDeliveryRejected(
            f"finding id {finding_id!r} is not a well-formed {IdPrefix.FINDING}_<ULID> id", fault
        )
    live_scope = live_findings.get(finding_id)
    if live_scope is None:
        if finding_id in exited_ids:
            raise GardenDeliveryRejected(
                f"finding {finding_id!r} has been exited on routine {run.routine_name!r} and cannot be transformed "
                "by a run — only a person's reopen can revive it",
                fault,
            )
        raise GardenDeliveryRejected(f"finding {finding_id!r} is not live on routine {run.routine_name!r}", fault)
    if scope is not None and live_scope != scope:
        raise GardenDeliveryRejected(
            f"finding {finding_id!r} is outside the declared scope {scope!r} (its own scope is {live_scope!r})",
            fault,
        )


def _check_commit_wellformed(sha: str, *, context: str) -> None:
    if not _COMMIT_RE.fullmatch(sha):
        raise GardenDeliveryRejected(
            f"commit {sha!r} ({context}) is not a well-formed commit sha", GardenDeliveryFault.DELTA
        )


def _resolved(repo: str, sha: str, resolutions: CommitResolutions | None) -> CommitResolution | None:
    _check_commit_wellformed(sha, context=f"repository {repo!r}")
    resolution = (resolutions or {}).get((repo, sha))
    if resolution is not None and not resolution.exists:
        raise GardenDeliveryRejected(
            f"commit {sha!r} for repository {repo!r} does not resolve", GardenDeliveryFault.DELTA
        )
    return resolution
