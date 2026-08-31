"""Delivery validation (blizzard#393 Phase 2) — the hub-executed delivery node's own
check, before anything is written (blizzard-product:/plans/garden/machinery.md
§Delivery): "It validates each artifact's shape and nothing else: that it parses, that
required fields are present, that ids are well-formed, that every finding id a
transformation or a proposal names is live on this routine, and that any commit cited
resolves." This module is that check, and only that check — pure functions over
already-loaded objects (`bzh:domain-takes-objects`), no I/O and no store/repository
calls. Materializing a passing result (minting rows, applying transformations) is a
later phase's own concern."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError

from blizzard.foundation.ids import FINDING_PREFIX, Id
from blizzard.hub.domain.run_context import RunContext
from blizzard.wire.finding import AddFindingOp, FindingDelta, GoneFindingOp
from blizzard.wire.garden_proposal import GardenProposalCandidate

# Lowercase hex, 7-40 characters — a well-formed commit sha's shape. Says nothing about
# whether the commit actually exists; that is `CommitResolver`'s question.
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")

_PROPOSALS_ADAPTER: TypeAdapter[list[GardenProposalCandidate]] = TypeAdapter(list[GardenProposalCandidate])


class GardenDeliveryRejected(Exception):
    """A delivered artifact failed validation. This exception's own message is the
    operator-legible reason (blizzard-product:/plans/garden/machinery.md §Delivery: "A
    rejected artifact routes back into the graph with the failure attached and nothing
    written") — never a raw pydantic error, never a stack of causes a person has to
    untangle."""


# "Every finding currently live on this routine" (machinery.md §Delivery), keyed by
# finding id, valued by that finding's own recorded scope slug — the shape a later
# phase queries the finding store for once (e.g. `IReadFindingRepository.list_for`)
# before calling in here.
LiveFindings = Mapping[str, str]

# Resolves whether `commit_sha` exists in `repo`. Returns `True`/`False` when `repo` is
# addressable, `None` when it is not (no forge configured for that repository) — a
# `None` result degrades that one commit's check to well-formedness only, exactly like
# passing `resolve_commit=None` to `validate_delivery`/`check_delta` degrades every
# commit's check. This module never constructs a resolver, only calls the one it is
# given — binding it to a real `ICommitResolver` is a later phase's job.
CommitResolver = Callable[[str, str], bool | None]


@dataclass(frozen=True)
class ValidatedDelivery:
    """What a passing :func:`validate_delivery` hands the next phase: the run it was
    delivered under and the parsed, checked deltas and proposal candidates to build a
    `DeliveryPlan` from — nothing here is durable yet (machinery.md §Delivery)."""

    run: RunContext
    deltas: list[FindingDelta]
    proposals: list[GardenProposalCandidate]


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
) -> None:
    """Validate one already-parsed delta against `run` and `live_findings`, raising
    :class:`GardenDeliveryRejected` on the first failure:

    - every repository's revision in `delta.revisions` is a well-formed commit sha,
      and — when `resolve_commit` is given and addresses that repository — resolves;
    - every `observed`/`gone` op's `id` is a well-formed `fin_<ULID>`, live on
      `run.routine_name`, and inside `delta.scope` (its live scope, from
      `live_findings`, must equal `delta.scope`);
    - an `add` op's `introduced` commit, when present, is always checked for
      well-formedness; it is additionally resolved only when `delta.revisions` names
      exactly one repository — `introduced` carries no repository of its own, so a
      delta spanning zero or several repositories leaves it ambiguous which one to
      resolve against, and the check degrades to well-formedness only;
    - a `gone` op's `note` is non-empty (an `observed` op carries nothing else to
      check)."""
    for repo, sha in delta.revisions.items():
        _check_commit_resolves(repo, sha, resolve_commit)

    single_repo = next(iter(delta.revisions)) if len(delta.revisions) == 1 else None
    for op in delta.findings:
        if isinstance(op, AddFindingOp):
            if op.introduced is not None:
                if single_repo is not None:
                    _check_commit_resolves(single_repo, op.introduced, resolve_commit)
                else:
                    _check_commit_wellformed(op.introduced, context="a finding addition's introduced commit")
            continue
        _check_known_id(op.id, run=run, live_findings=live_findings, scope=delta.scope)
        if isinstance(op, GoneFindingOp) and not op.note.strip():
            raise GardenDeliveryRejected(f"finding {op.id!r}'s gone fact must carry a non-empty note")


def check_proposal(proposal: GardenProposalCandidate, *, run: RunContext, live_findings: LiveFindings) -> None:
    """Validate one already-parsed proposal candidate: every id in `proposal.findings`
    must be a well-formed `fin_<ULID>` and live on `run.routine_name` — a proposal
    carries no scope of its own to check the finding against (unlike a delta's
    transformations, see :func:`check_delta`)."""
    for finding_id in proposal.findings:
        _check_known_id(finding_id, run=run, live_findings=live_findings, scope=None)


def validate_delivery(
    *,
    run: RunContext,
    delta_artifacts: Mapping[str, str],
    proposal_artifacts: Mapping[str, str],
    live_findings: LiveFindings,
    resolve_commit: CommitResolver | None = None,
) -> ValidatedDelivery:
    """The delivery node's whole check: `delta_artifacts` and `proposal_artifacts` are
    artifact-name → raw-JSON-text maps, exactly what a route handler holds after
    reading each named artifact's content and before anything is parsed. Parses every
    artifact (:func:`parse_delta`/:func:`parse_proposals`), then validates every parsed
    delta and proposal (:func:`check_delta`/:func:`check_proposal`) against `run` and
    `live_findings`, resolving cited commits through `resolve_commit` (see
    :data:`CommitResolver` for its degrade contract). Raises
    :class:`GardenDeliveryRejected` on the first failure; on success, returns a
    :class:`ValidatedDelivery` for the next phase to materialize — nothing here is
    durable yet."""
    deltas = [parse_delta(name, raw) for name, raw in delta_artifacts.items()]
    proposals = [candidate for name, raw in proposal_artifacts.items() for candidate in parse_proposals(name, raw)]

    for delta in deltas:
        check_delta(delta, run=run, live_findings=live_findings, resolve_commit=resolve_commit)
    for proposal in proposals:
        check_proposal(proposal, run=run, live_findings=live_findings)

    return ValidatedDelivery(run=run, deltas=deltas, proposals=proposals)


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
    if not _COMMIT_RE.match(sha):
        raise GardenDeliveryRejected(f"commit {sha!r} ({context}) is not a well-formed commit sha")


def _check_commit_resolves(repo: str, sha: str, resolver: CommitResolver | None) -> None:
    _check_commit_wellformed(sha, context=f"repository {repo!r}")
    if resolver is None:
        return
    if resolver(repo, sha) is False:
        raise GardenDeliveryRejected(f"commit {sha!r} for repository {repo!r} does not resolve")


def _summarize(exc: ValidationError) -> str:
    """A short, operator-legible rendering of `exc` — location and message per error,
    never pydantic's own multi-line `str()` with its "further information" links."""
    parts = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"]) or "<root>"
        parts.append(f"{loc}: {error['msg']}")
    return "; ".join(parts)
