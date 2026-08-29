"""SQLAlchemy adapter for the garden-proposal repository seam (package-private,
blizzard#390). All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``).
The findings a proposal answers are a join over ``garden_proposal_findings`` (D7), never
a JSON column."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine, func, insert, select

from blizzard.hub.domain.garden_proposals import GardenProposal, IWriteGardenProposalRepository
from blizzard.hub.store.schema import garden_proposal_findings, garden_proposals


class GardenProposalStore:
    """Read-write garden-proposal adapter over the hub store engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

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
        with self._engine.begin() as conn:
            conn.execute(
                insert(garden_proposals).values(
                    proposal_id=proposal_id,
                    routine_name=routine_name,
                    class_=class_,
                    title=title,
                    body=body,
                    created_at=at,
                )
            )
            conn.execute(
                insert(garden_proposal_findings),
                [{"proposal_id": proposal_id, "finding_id": finding_id} for finding_id in findings],
            )
        return GardenProposal(
            proposal_id=proposal_id,
            routine_name=routine_name,
            class_=class_,
            title=title,
            body=body,
            created_at=at,
            findings=list(findings),
        )

    def get(self, proposal_id: str) -> GardenProposal | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(garden_proposals).where(garden_proposals.c.proposal_id == proposal_id)
            ).one_or_none()
            if row is None:
                return None
            return self._of(row, self._findings(conn, proposal_id))

    def list_all(self) -> list[GardenProposal]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(garden_proposals).order_by(garden_proposals.c.created_at.desc())).all()
            return [self._of(row, self._findings(conn, row.proposal_id)) for row in rows]

    def count_by_class(self, routine_name: str, class_: str) -> int:
        with self._engine.connect() as conn:
            return conn.execute(
                select(func.count())
                .select_from(garden_proposals)
                .where(garden_proposals.c.routine_name == routine_name, garden_proposals.c.class_ == class_)
            ).scalar_one()

    def _findings(self, conn, proposal_id: str) -> list[str]:  # type: ignore[no-untyped-def]
        rows = conn.execute(
            select(garden_proposal_findings.c.finding_id).where(garden_proposal_findings.c.proposal_id == proposal_id)
        ).all()
        return [r.finding_id for r in rows]

    @staticmethod
    def _of(row, findings: list[str]) -> GardenProposal:  # type: ignore[no-untyped-def]
        return GardenProposal(
            proposal_id=row.proposal_id,
            routine_name=row.routine_name,
            class_=row.class_,
            title=row.title,
            body=row.body,
            created_at=row.created_at,
            findings=findings,
        )


def _conforms_garden_proposal_store(x: GardenProposalStore) -> IWriteGardenProposalRepository:
    return x
