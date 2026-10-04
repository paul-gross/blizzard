"""A garden run's finding bucket — the one owner of which findings a routine run is shown
and may cite. The worker-facing read and delivery validation both take it from
here, so the set a run sees and the set its delivery may cite are the same by construction."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.garden.findings.model import Finding, IReadFindingRepository
from blizzard.hub.domain.garden.run_context import RunContext


@domain_model
@dataclass(frozen=True)
class FindingBucket:
    """What a run may cite: `citable` is every non-exited finding, the run's own scope
    first, then its neighbour scopes, each group ordered by `finding_id`. `exited_ids`
    are the bucket's exited findings' ids, held only so delivery can tell an exited
    id from an unknown one — never shown."""

    citable: list[Finding]
    exited_ids: frozenset[str]

    @classmethod
    def of(cls, rows: Sequence[Finding], *, own_scope: str) -> FindingBucket:
        """The bucket over `rows`, which may name one finding twice (a review-sourced
        finding the routine also holds): every unexited finding is citable, `own_scope`
        first; every exited one is only an id."""
        unique = {f.finding_id: f for f in rows}.values()
        citable = sorted((f for f in unique if not f.exited), key=lambda f: (f.scope_slug != own_scope, f.finding_id))
        return cls(citable=citable, exited_ids=frozenset(f.finding_id for f in unique if f.exited))


class FindingBucketReader:
    """Reads a run's :class:`FindingBucket`: its routine's own findings across every
    scope, plus review-sourced findings on the run's own scope. Unpaged by design — a run
    must see everything it may cite, so no page can bound the read; it stays two bulk
    reads however many findings the bucket holds."""

    def __init__(self, findings: IReadFindingRepository) -> None:
        self._findings = findings

    def for_run(self, run: RunContext) -> FindingBucket:
        rows = self._findings.list_for_routine(run.routine_name, include_gone=True)
        rows += self._findings.list_by_source(scope_slug=run.scope_slug, source="review", include_gone=True)
        return FindingBucket.of(rows, own_scope=run.scope_slug)
