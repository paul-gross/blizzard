"""Rendering flows and the places chunks hold in them as the board and the CLI read them. The places are the domain's
own (:meth:`FlowLine.places_of`); nothing here judges a state."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.roles import collaborator
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryId
from blizzard.hub.domain.delivery.flows import Flow, FlowFact, FlowLine, FlowOverview, FlowPlace, Submission, Turn
from blizzard.wire.flow import (
    ChunkFlowPlaceView,
    FlowFactKind,
    FlowFactView,
    FlowPlaceState,
    FlowPlaceView,
    FlowTurnView,
    FlowView,
)


def fact_view(fact: FlowFact) -> FlowFactView:
    return FlowFactView(
        id=fact.id,
        flow_id=fact.flow_id,
        submission_id=fact.submission_id,
        chunk_id=fact.chunk_id,
        kind=FlowFactKind(fact.kind),
        at=iso_utc(fact.at),
        outcome=fact.outcome,
        revision=fact.revision,
    )


@collaborator
@dataclass(frozen=True)
class FlowLineRead:
    """One read of a set of submissions' line — the submissions, the open turns among them, the line judged over
    them, and the records of the repositories their flows land on — at one instant."""

    line: FlowLine
    turns: Mapping[str, Turn]
    repositories: Mapping[RepositoryId, ConfiguredRepository]
    now: datetime

    @classmethod
    def of(cls, services: TenantServices, submissions: Sequence[Submission]) -> FlowLineRead:
        """The line over ``submissions``, which must hold every open submission on each flow any of them holds — a
        place depends on every submission ahead of it there, on whatever page that submission's chunk sits."""
        turns = {turn.submission_id: turn for turn in services.flows.open_turns()} if submissions else {}
        repository_ids = sorted({RepositoryId(flow.repository_id) for sub in submissions for flow in sub.flows})
        return cls(
            line=services.flow_line_factory.line(submissions, turning=frozenset(turns)),
            turns=turns,
            repositories=services.repository_records.get_many(repository_ids) if repository_ids else {},
            now=services.clock.now(),
        )

    @classmethod
    def around(cls, services: TenantServices, flow_ids: Sequence[str]) -> FlowLineRead:
        """The line of every open submission on ``flow_ids``, and on every other flow one of those holds — whether
        a submission is next turns on each flow it holds."""
        held = services.flow_views.open_submissions_on(flow_ids) if flow_ids else []
        wider = sorted({flow.flow_id for sub in held for flow in sub.flows} - set(flow_ids))
        return cls.of(services, services.flow_views.open_submissions_on([*flow_ids, *wider]) if wider else held)

    def _repository(self, flow: Flow) -> ConfiguredRepository | None:
        return self.repositories.get(RepositoryId(flow.repository_id))

    def chunk_places(self, submissions: Sequence[Submission]) -> dict[str, list[ChunkFlowPlaceView]]:
        """Each of ``submissions``' places, by chunk id, in arrival order then each submission's ``flows`` order."""
        places: dict[str, list[ChunkFlowPlaceView]] = {}
        for sub in submissions:
            for place in self.line.places_of(sub, now=self.now):
                places.setdefault(sub.chunk_id, []).append(self._chunk_place(place))
        return places

    def _chunk_place(self, place: FlowPlace) -> ChunkFlowPlaceView:
        record = self._repository(place.flow)
        return ChunkFlowPlaceView(
            flow_id=place.flow.flow_id,
            resource=place.flow.resource,
            repository_slug=record.slug if record is not None else None,
            repository_name=record.name if record is not None else None,
            branch=place.flow.branch,
            state=FlowPlaceState(place.state),
            position=place.position,
            next_poll_at=iso_utc(place.due_at) if place.due_at is not None else None,
        )

    def in_line(self, flow_id: str) -> bool:
        return any(flow.flow_id == flow_id for sub in self.line.submissions for flow in sub.flows)

    def flow_view(self, overview: FlowOverview) -> FlowView:
        """``overview``'s flow with its open turn and its line in arrival order."""
        flow = overview.flow
        record = self._repository(flow)
        holding = [sub for sub in self.line.submissions if any(f.flow_id == flow.flow_id for f in sub.flows)]
        turning = next(
            (
                FlowTurnView(chunk_id=sub.chunk_id, started_at=iso_utc(turn.started_at))
                for sub in holding
                if (turn := self.turns.get(sub.submission_id)) is not None
            ),
            None,
        )
        line = [
            FlowPlaceView(
                chunk_id=place.submission.chunk_id,
                submission_id=place.submission.submission_id,
                arrival=place.submission.arrival,
                state=FlowPlaceState(place.state),
                position=place.position,
                next_poll_at=iso_utc(place.due_at) if place.due_at is not None else None,
            )
            for sub in holding
            for place in self.line.places_of(sub, now=self.now)
            if place.flow.flow_id == flow.flow_id
        ]
        return FlowView(
            flow_id=flow.flow_id,
            resource=flow.resource,
            repository_id=flow.repository_id,
            repository_slug=record.slug if record is not None else None,
            repository_name=record.name if record is not None else None,
            branch=flow.branch,
            turn=turning,
            line=line,
            last_verdict=fact_view(overview.last_verdict) if overview.last_verdict is not None else None,
            last_landing=fact_view(overview.last_landing) if overview.last_landing is not None else None,
        )


def chunk_flow_places(services: TenantServices, chunk_ids: Sequence[str]) -> dict[str, list[ChunkFlowPlaceView]]:
    """Each named chunk's places, by chunk id; a chunk with no open submission holding a flow has no entry.

    A place depends on the submissions ahead of it, whose chunks may sit on no page that names them, so the line is
    read whole over the flows the chunks hold — a fixed number of batched reads bounded by the line, not the fleet.
    The places are those of the chunks' submissions in that line, not in the first read: one that closed in between
    is in neither the line nor its facts."""
    own = services.flow_views.open_submissions_of(chunk_ids) if chunk_ids else []
    flow_ids = sorted({flow.flow_id for sub in own for flow in sub.flows})
    if not flow_ids:
        return {}
    read = FlowLineRead.of(services, services.flow_views.open_submissions_on(flow_ids))
    named = set(chunk_ids)
    return read.chunk_places([sub for sub in read.line.submissions if sub.chunk_id in named])
