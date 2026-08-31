"""Delivery validation (blizzard#393 Phase 2) — the hub-executed delivery node's shape
check, before anything is written; the check itself is specified by
blizzard-product:/plans/garden/machinery.md §Delivery. Pure functions over already-loaded
objects (`bzh:domain-takes-objects`), no I/O. Materializing a passing result is
`garden_delivery_materialize.py`'s."""

from __future__ import annotations

import functools
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import TypeAdapter, ValidationError

from blizzard.foundation.ids import FINDING_PREFIX, Id
from blizzard.hub.domain.findings import EXIT_KINDS, Finding
from blizzard.hub.domain.run_context import RunContext
from blizzard.wire.finding import AddFindingOp, FindingDelta, GoneFindingOp
from blizzard.wire.garden_proposal import GardenProposalCandidate

# Lowercase hex, 7-40 characters — a well-formed commit sha's shape. Says nothing about
# whether the commit actually exists; that is `CommitResolver`'s question.
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")

_PROPOSALS_ADAPTER: TypeAdapter[list[GardenProposalCandidate]] = TypeAdapter(list[GardenProposalCandidate])


class GardenDeliveryRejected(Exception):
    """A delivered artifact failed validation. This exception's own message is the
    operator-legible reason the graph attaches to its bounce — never a raw pydantic
    error, never a stack of causes a person has to untangle."""


# Every finding known to this routine, live or gone (a later `observed` may revive a
# `gone` one) — minus an exited one (blizzard#394 D3): human-driven exit is terminal to a
# run's delta ops. Keyed by finding id, valued by that finding's own recorded scope slug.
LiveFindings = Mapping[str, str]


# Whether `(repo, commit)` resolves, and — when it does — the commit's own authored
# instant (blizzard#394 D5): the GitHub adapter already fetches the commit body carrying
# it, so widening this to carry the instant costs no extra forge round trip.
@dataclass(frozen=True)
class CommitResolution:
    exists: bool
    authored_at: datetime | None = None


# Resolves `(repo, commit_sha)`: a :class:`CommitResolution` when `repo` is addressable,
# `None` when it is not, degrading that commit to a well-formedness check.
CommitResolver = Callable[[str, str], CommitResolution | None]


@dataclass(frozen=True)
class ValidatedDelivery:
    """What a passing :func:`validate_delivery` hands the next phase: the run it was
    delivered under and the parsed, checked deltas and proposal candidates to build a
    `DeliveryPlan` from — nothing here is durable yet (machinery.md §Delivery).
    `proposal_sources[i]` is the artifact name `proposals[i]` came from, positionally.
    `introduced_at[(repo, sha)]` is that pair's own resolved authored instant (blizzard#394
    D5) — `None` when the resolver ran but found none, absent when never resolved; either
    way, materialization must not re-resolve to fill the gap (D5: no backfill)."""

    run: RunContext
    deltas: list[FindingDelta]
    proposals: list[GardenProposalCandidate]
    proposal_sources: list[str] = field(default_factory=list)
    introduced_at: dict[tuple[str, str], datetime | None] = field(default_factory=dict)


def parse_delta(artifact_name: str, raw: str) -> FindingDelta:
    """Parse `raw` as JSON and validate it against :class:`FindingDelta`. Both a JSON
    syntax failure and a shape mismatch surface as pydantic `ValidationError` from
    `model_validate_json` and become one :class:`GardenDeliveryRejected`, naming
    `artifact_name` rather than dumping the raw pydantic error."""
    try:
        return FindingDelta.model_validate_json(raw)
    except ValidationError as exc:
        raise GardenDeliveryRejected(
            f"artifact {artifact_name!r} does not match the finding-delta shape: {_summarize(exc)}"
        ) from exc


def parse_proposals(artifact_name: str, raw: str) -> list[GardenProposalCandidate]:
    """Parse `raw` as JSON and validate it against `list[GardenProposalCandidate]` —
    the shape of a `--proposals` artifact. See :func:`parse_delta` for the error
    contract."""
    try:
        return _PROPOSALS_ADAPTER.validate_json(raw)
    except ValidationError as exc:
        raise GardenDeliveryRejected(
            f"artifact {artifact_name!r} does not match the garden-proposal-candidate shape: {_summarize(exc)}"
        ) from exc


def check_delta(
    delta: FindingDelta,
    *,
    run: RunContext,
    live_findings: LiveFindings,
    resolve_commit: CommitResolver | None = None,
) -> dict[tuple[str, str], datetime | None]:
    """Validate one already-parsed delta against `run` and `live_findings`, raising
    :class:`GardenDeliveryRejected` on the first failure: `delta.scope` against `run`'s
    own declared scope, every revision's and `add`'s commit sha, every transformation's
    id (well-formed, known to the routine, in scope), and a `gone` op's non-empty note.
    `tests/test_garden_delivery_domain.py` pins one case per rejection reason. Returns
    every `(repo, sha)` this delta resolved for an `add`'s `introduced` commit, keyed for
    `ValidatedDelivery.introduced_at` (blizzard#394 D5) — never the revision shas, which
    name no finding of their own."""
    if delta.scope != run.scope_slug:
        raise GardenDeliveryRejected(
            f"delta declares scope {delta.scope!r}, this run's declared scope is {run.scope_slug!r}"
        )
    for repo, sha in delta.revisions.items():
        _check_commit_resolves(repo, sha, resolve_commit)

    introduced_at: dict[tuple[str, str], datetime | None] = {}
    single_repo = next(iter(delta.revisions)) if len(delta.revisions) == 1 else None
    for op in delta.findings:
        if isinstance(op, AddFindingOp):
            if op.introduced is not None:
                # `introduced` names no repository of its own, so it resolves only against
                # a sole declared one; zero or several leave which one ambiguous, and its
                # instant stays unattributed (D5's own third bucket).
                if single_repo is not None:
                    resolution = _check_commit_resolves(single_repo, op.introduced, resolve_commit)
                    introduced_at[(single_repo, op.introduced)] = resolution.authored_at if resolution else None
                else:
                    _check_commit_wellformed(op.introduced, context="a finding addition's introduced commit")
            continue
        _check_known_id(op.id, run=run, live_findings=live_findings, scope=delta.scope)
        if isinstance(op, GoneFindingOp) and not op.note.strip():
            raise GardenDeliveryRejected(f"finding {op.id!r}'s gone fact must carry a non-empty note")
    return introduced_at


def check_proposal(proposal: GardenProposalCandidate, *, run: RunContext, live_findings: LiveFindings) -> None:
    """Validate one already-parsed proposal candidate: every id in `proposal.findings`
    must be a well-formed `fin_<ULID>` and live on `run.routine_name` — a proposal
    carries no scope of its own to check the finding against (unlike a delta's
    transformations, see :func:`check_delta`)."""
    for finding_id in proposal.findings:
        _check_known_id(finding_id, run=run, live_findings=live_findings, scope=None)


def check_proposal_refs(artifact_name: str, proposals: Sequence[GardenProposalCandidate]) -> None:
    """Validate that one artifact's candidates carry distinct `ref`s. A delivered
    proposal is identified by its source artifact plus its submission-local `ref`, so an
    artifact naming one twice says two different proposals are the same one — rejected
    here as a legible bounce rather than left to collide at insert. Two *different*
    artifacts reusing a `ref` name unrelated proposals and are fine."""
    seen: set[str] = set()
    for proposal in proposals:
        if proposal.ref in seen:
            raise GardenDeliveryRejected(
                f"artifact {artifact_name!r} carries the proposal ref {proposal.ref!r} more than once"
            )
        seen.add(proposal.ref)


def validate_delivery(
    *,
    run: RunContext,
    delta_artifacts: Mapping[str, str],
    proposal_artifacts: Mapping[str, str],
    known_findings: Sequence[Finding],
    resolve_commit: CommitResolver | None = None,
) -> ValidatedDelivery:
    """The delivery node's whole check. `delta_artifacts`/`proposal_artifacts` are
    artifact-name → raw-JSON-text maps, what a route handler holds before parsing;
    `known_findings` is every finding on `run.routine_name`, live or gone, minus an
    exited one (blizzard#394 D3). Parses each artifact, then checks it against `run`,
    raising :class:`GardenDeliveryRejected` on the first failure; on success returns a
    :class:`ValidatedDelivery`, nothing durable."""
    live_findings: LiveFindings = {f.finding_id: f.scope_slug for f in known_findings if f.state not in EXIT_KINDS}
    deltas = [parse_delta(name, raw) for name, raw in delta_artifacts.items()]
    proposals: list[GardenProposalCandidate] = []
    proposal_sources: list[str] = []
    for name, raw in proposal_artifacts.items():
        candidates = parse_proposals(name, raw)
        check_proposal_refs(name, candidates)
        proposals.extend(candidates)
        proposal_sources.extend(name for _ in candidates)

    if resolve_commit is not None:
        # Memoize per delivery: many findings sharing one `introduced` commit must not
        # spend the fleet-wide hub-exec slot twice. Built fresh here, so never stale.
        resolve_commit = functools.lru_cache(maxsize=None)(resolve_commit)

    introduced_at: dict[tuple[str, str], datetime | None] = {}
    for delta in deltas:
        introduced_at.update(check_delta(delta, run=run, live_findings=live_findings, resolve_commit=resolve_commit))
    for proposal in proposals:
        check_proposal(proposal, run=run, live_findings=live_findings)

    return ValidatedDelivery(
        run=run,
        deltas=deltas,
        proposals=proposals,
        proposal_sources=proposal_sources,
        introduced_at=introduced_at,
    )


def _check_known_id(finding_id: str, *, run: RunContext, live_findings: LiveFindings, scope: str | None) -> None:
    parsed = Id.parse(finding_id)
    if parsed is None or not parsed.has_prefix(FINDING_PREFIX):
        raise GardenDeliveryRejected(f"finding id {finding_id!r} is not a well-formed {FINDING_PREFIX}_<ULID> id")
    live_scope = live_findings.get(finding_id)
    if live_scope is None:
        raise GardenDeliveryRejected(f"finding {finding_id!r} is not live on routine {run.routine_name!r}")
    if scope is not None and live_scope != scope:
        raise GardenDeliveryRejected(
            f"finding {finding_id!r} is outside the declared scope {scope!r} (its own scope is {live_scope!r})"
        )


def _check_commit_wellformed(sha: str, *, context: str) -> None:
    if not _COMMIT_RE.fullmatch(sha):
        raise GardenDeliveryRejected(f"commit {sha!r} ({context}) is not a well-formed commit sha")


def _check_commit_resolves(repo: str, sha: str, resolver: CommitResolver | None) -> CommitResolution | None:
    _check_commit_wellformed(sha, context=f"repository {repo!r}")
    if resolver is None:
        return None
    resolution = resolver(repo, sha)
    if resolution is not None and not resolution.exists:
        raise GardenDeliveryRejected(f"commit {sha!r} for repository {repo!r} does not resolve")
    return resolution


def _summarize(exc: ValidationError) -> str:
    """A short, operator-legible rendering of `exc` — location and message per error,
    never pydantic's own multi-line `str()` with its "further information" links."""
    parts = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"]) or "<root>"
        parts.append(f"{loc}: {error['msg']}")
    return "; ".join(parts)
