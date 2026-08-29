"""Garden-proposal domain model — a proposed response to one or more findings
(blizzard#390). Named `garden_proposals`/`GardenProposal` throughout — never the bare
`proposal`/`Proposal` a work-item proposal already claims (D1)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import GARDEN_PROPOSAL_PREFIX, Id
from blizzard.hub.domain.findings import Finding


class EmptyProposalFindingsError(ValueError):
    """A proposal names no findings (D7) — required and non-empty."""

    def __init__(self) -> None:
        super().__init__("a garden proposal must name at least one finding")


class DuplicateProposalFindingError(ValueError):
    """The same finding named more than once in one proposal's `findings`."""

    def __init__(self, finding_id: str) -> None:
        super().__init__(f"finding {finding_id!r} named more than once")


@dataclass(frozen=True)
class GardenProposal:
    proposal_id: str
    routine_name: str
    class_: str
    title: str
    body: str
    created_at: datetime
    findings: list[str] = field(default_factory=list)  # the finding ids this proposal answers (D7)


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


class IReadGardenProposalRepository(Protocol):
    """Read-only garden-proposal access. Controllers at the edges depend on this variant."""

    def get(self, proposal_id: str) -> GardenProposal | None: ...

    def list_all(self) -> list[GardenProposal]: ...

    def count_by_class(self, routine_name: str, class_: str) -> int:
        """How often `class_` recurs among `routine_name`'s proposals
        (blizzard-context:/domain/findings-and-proposals.md §`class` and `locus` are
        opaque) — a count, never the rows themselves."""
        ...


class IWriteGardenProposalRepository(IReadGardenProposalRepository, Protocol):
    """Read-write garden-proposal access. Only the domain layer depends on this variant."""

    def create(
        self,
        proposal_id: str,
        *,
        routine_name: str,
        class_: str,
        title: str,
        body: str,
        findings: list[str],
        at: datetime,
    ) -> GardenProposal:
        """Insert the proposal row and its `garden_proposal_findings` link rows (D7), in
        one transaction. Non-empty `findings` is enforced by
        :class:`GardenProposalAuthoring` before this is called."""
        ...


class GardenProposalAuthoring:
    """Create a garden proposal from loaded findings (`bzh:domain-takes-objects`),
    rejecting an empty or duplicate-naming `findings` list (D7, blizzard#390)."""

    def __init__(self, *, proposals: IWriteGardenProposalRepository, clock: IClock) -> None:
        self._proposals = proposals
        self._clock = clock

    def create(
        self, *, routine_name: str, class_: str, title: str, body: str, findings: Sequence[Finding]
    ) -> GardenProposal:
        if not findings:
            raise EmptyProposalFindingsError()
        finding_ids = [f.finding_id for f in findings]
        seen: set[str] = set()
        for finding_id in finding_ids:
            if finding_id in seen:
                raise DuplicateProposalFindingError(finding_id)
            seen.add(finding_id)
        return self._proposals.create(
            Id.mint(GARDEN_PROPOSAL_PREFIX, self._clock).value,
            routine_name=routine_name,
            class_=class_,
            title=title,
            body=body,
            findings=finding_ids,
            at=self._clock.now(),
        )
