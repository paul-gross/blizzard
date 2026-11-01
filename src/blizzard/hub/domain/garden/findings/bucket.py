"""A garden run's finding bucket — the one owner of which findings a routine run is shown
and may cite."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.garden.findings.model import Finding, IReadFindingRepository
from blizzard.hub.domain.garden.run_context import RunContext


@domain_model
@dataclass(frozen=True)
class FindingBucket:
    """What a run may cite: `citable` is every non-exited finding, the run's own scope
    first, then its neighbour scopes, each group ordered by `finding_id`. An exited
    finding is never part of the bucket — delivery asks :meth:`FindingBucketReader.exited_among`
    about only the ids a delivery cites."""

    citable: list[Finding]

    @classmethod
    def of(cls, rows: Sequence[Finding], *, own_scope: str) -> FindingBucket:
        """The bucket over `rows`, which may name one finding twice (a review-sourced
        finding the routine also holds): every unexited finding is citable, `own_scope` first. The
        store already drops exited findings; the rule is restated here so the bucket holds it
        whatever feeds it."""
        unique = {f.finding_id: f for f in rows}.values()
        citable = sorted((f for f in unique if not f.exited), key=lambda f: (f.scope_slug != own_scope, f.finding_id))
        return cls(citable=citable)


def is_exited_citable(finding: Finding, run: RunContext) -> bool:
    """Whether `finding` is exited yet one `run` could otherwise have cited: same project, and
    either the routine's own finding in any scope or a review-sourced finding on the run's scope."""
    return (
        finding.exited
        and finding.project_id == run.project_id
        and (
            finding.routine_name == run.routine_name
            or (finding.source == "review" and finding.scope_slug == run.scope_slug)
        )
    )


class FindingBucketReader:
    """Reads a run's :class:`FindingBucket`: its routine's own non-exited findings across every
    scope, plus non-exited review-sourced findings on the run's own scope — all within the run's
    project, so a same-slug scope of another project never joins. Exited rows are excluded per
    :meth:`IReadFindingRepository.list_for_routine`'s `exclude_exited`."""

    def __init__(self, findings: IReadFindingRepository) -> None:
        self._findings = findings

    @property
    def findings(self) -> IReadFindingRepository:
        return self._findings

    def for_run(self, run: RunContext) -> FindingBucket:
        rows = self._findings.list_for_routine(run.project_id, run.routine_name, include_gone=True, exclude_exited=True)
        rows += self._findings.list_by_source(
            run.project_id, scope_slug=run.scope_slug, source="review", include_gone=True, exclude_exited=True
        )
        return FindingBucket.of(rows, own_scope=run.scope_slug)

    def exited_among(self, run: RunContext, finding_ids: Collection[str]) -> frozenset[str]:
        """Which of `finding_ids` satisfy :func:`is_exited_citable`, in one bulk read, so delivery
        can tell an exited id from an unknown one."""
        if not finding_ids:
            return frozenset()
        return frozenset(
            f.finding_id for f in self._findings.get_many(sorted(finding_ids)).values() if is_exited_citable(f, run)
        )
