"""Garden-proposal domain model — a proposed response to one or more findings
(blizzard#390). Named `garden_proposals`/`GardenProposal` throughout — never the bare
`proposal`/`Proposal` a work-item proposal already claims (D1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import GARDEN_PROPOSAL_PREFIX, Id


class EmptyProposalFindingsError(ValueError):
    """A proposal names no findings (D7) — required and non-empty."""

    def __init__(self) -> None:
        super().__init__("a garden proposal must name at least one finding")


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
        """How often `class_` recurs among `routine_name`'s proposals (machinery.md
        §Proposals) — a count, never the rows themselves."""
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
    """Create a garden proposal, rejecting an empty `findings` list (D7, blizzard#390)."""

    def __init__(self, *, proposals: IWriteGardenProposalRepository, clock: IClock) -> None:
        self._proposals = proposals
        self._clock = clock

    def create(self, *, routine_name: str, class_: str, title: str, body: str, findings: list[str]) -> GardenProposal:
        if not findings:
            raise EmptyProposalFindingsError()
        return self._proposals.create(
            Id.mint(GARDEN_PROPOSAL_PREFIX, self._clock).value,
            routine_name=routine_name,
            class_=class_,
            title=title,
            body=body,
            findings=list(findings),
            at=self._clock.now(),
        )
