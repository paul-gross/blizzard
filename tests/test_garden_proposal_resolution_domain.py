"""``GardenProposalDeliveryResolution`` (unit tier, blizzard#394 Phase 3) and
``AnsweredFindingsReader`` (blizzard#397 Phase 1): the write-side delivery resolution
resolves a proposal's still-live findings only when its own closure is an accepted,
minting one naming the delivered pointer — a pass, a decline, an absent closure, an
absent proposal, or an already-exited finding all resolve nothing. The read-side
resolution answers the same accepted-mint closure's proposal findings for a chunk, or
``None`` when the chunk carries no work ref, its item names no such closure, or the
closure names a proposal that no longer resolves. Plain unit tests over fakes
(``bzh:domain-takes-objects``), the ``tests/test_finding_exit_service.py`` shape."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.findings import FactEntry, Finding, FindingExitService
from blizzard.hub.domain.garden_proposal_closure import (
    GardenProposalClosure,
    GardenProposalClosureKind,
    GardenProposalItemOutcome,
)
from blizzard.hub.domain.garden_proposal_resolution import AnsweredFindingsReader, GardenProposalDeliveryResolution
from blizzard.hub.domain.garden_proposals import GardenProposal
from blizzard.hub.domain.work import Chunk, WorkRef

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_POINTER = WorkRef(source="hub", ref="42")


def _finding(finding_id: str, *, state: str = "live") -> Finding:
    return Finding(
        finding_id=finding_id,
        routine_name="nightly",
        scope_slug="blizzard",
        class_="stale-docstring",
        locus="a.py:1",
        summary="s",
        introduced=None,
        introduced_at=None,
        first_observed_at=_T0,
        live=state == "live",
        state=state,
        note=None,
        last_seen_at=_T0,
        observed_count=0,
    )


def _proposal(proposal_id: str = "gprop_1", *, findings: list[str] | None = None) -> GardenProposal:
    return GardenProposal(
        proposal_id=proposal_id,
        routine_name="nightly",
        class_="fix-the-source",
        title="t",
        body="b",
        created_at=_T0,
        findings=findings if findings is not None else ["fin_1"],
    )


def _chunk(*, work_refs: list[WorkRef] | None = None) -> Chunk:
    return Chunk(
        chunk_id="ch_1",
        graph_id="g_1",
        work_refs=work_refs if work_refs is not None else [_POINTER],
        minted_at=_T0,
    )


def _closure(
    *, kind: GardenProposalClosureKind, item_outcome: GardenProposalItemOutcome | None, proposal_id: str = "gprop_1"
) -> GardenProposalClosure:
    return GardenProposalClosure(
        proposal_id=proposal_id,
        closure=kind,
        reason=None,
        closed_by="u_1",
        closed_at=_T0,
        item_outcome=item_outcome,
        source=_POINTER.source if item_outcome is GardenProposalItemOutcome.MINTED else None,
        ref=_POINTER.ref if item_outcome is GardenProposalItemOutcome.MINTED else None,
    )


@dataclass
class _FakeClosures:
    by_item: dict[tuple[str, str], GardenProposalClosure] = field(default_factory=dict)

    def get(self, proposal_id: str) -> GardenProposalClosure | None:
        raise NotImplementedError

    def get_many(self, proposal_ids: object) -> dict[str, GardenProposalClosure]:
        raise NotImplementedError

    def find_by_item(self, source: str, ref: str) -> GardenProposalClosure | None:
        return self.by_item.get((source, ref))


@dataclass
class _FakeProposals:
    by_id: dict[str, GardenProposal] = field(default_factory=dict)

    def get(self, proposal_id: str) -> GardenProposal | None:
        return self.by_id.get(proposal_id)

    def list_all(self) -> list[GardenProposal]:
        raise NotImplementedError

    def count_by_class(self, routine_name: str, class_: str) -> int:
        raise NotImplementedError


@dataclass
class _FakeFindings:
    by_id: dict[str, Finding] = field(default_factory=dict)
    resolved_proposal_ids: frozenset[str] = frozenset()

    def get(self, finding_id: str) -> Finding | None:
        return self.by_id.get(finding_id)

    def get_many(self, finding_ids: Sequence[str]) -> dict[str, Finding]:
        return {fid: f for fid in finding_ids if (f := self.by_id.get(fid)) is not None}

    def list_for(self, routine_name: str, scope_slug: str, *, include_gone: bool = False) -> list[Finding]:
        raise NotImplementedError

    def list_for_routine(self, routine_name: str, *, include_gone: bool = False) -> list[Finding]:
        raise NotImplementedError

    def count_by_class(self, routine_name: str, class_: str) -> int:
        raise NotImplementedError

    def has_resolution_for_proposal(self, proposal_id: str) -> bool:
        return proposal_id in self.resolved_proposal_ids


@dataclass
class _RecordingWriteRepo:
    batches: list[list[FactEntry]] = field(default_factory=list)

    def record_facts(self, entries: list[FactEntry]) -> None:
        self.batches.append(list(entries))


def _resolution(
    *, closures: _FakeClosures, proposals: _FakeProposals, findings: _FakeFindings, repo: _RecordingWriteRepo
) -> GardenProposalDeliveryResolution:
    exits = FindingExitService(repo=repo, clock=FixedClock(instant=_T0))  # type: ignore[arg-type]
    return GardenProposalDeliveryResolution(closures=closures, proposals=proposals, findings=findings, exits=exits)


def test_no_closure_for_the_item_resolves_nothing() -> None:
    repo = _RecordingWriteRepo()
    resolution = _resolution(closures=_FakeClosures(), proposals=_FakeProposals(), findings=_FakeFindings(), repo=repo)

    resolution.resolve_for_item(_POINTER)

    assert repo.batches == []


def test_a_passed_closure_resolves_nothing() -> None:
    closures = _FakeClosures(
        by_item={
            (_POINTER.source, _POINTER.ref): GardenProposalClosure(
                proposal_id="gprop_1",
                closure=GardenProposalClosureKind.PASSED,
                reason="not worth it",
                closed_by="u_1",
                closed_at=_T0,
                item_outcome=None,
                source=_POINTER.source,
                ref=_POINTER.ref,
            )
        }
    )
    repo = _RecordingWriteRepo()
    resolution = _resolution(closures=closures, proposals=_FakeProposals(), findings=_FakeFindings(), repo=repo)

    resolution.resolve_for_item(_POINTER)

    assert repo.batches == []


def test_an_accepted_but_declined_mint_resolves_nothing() -> None:
    closures = _FakeClosures(
        by_item={
            (_POINTER.source, _POINTER.ref): _closure(
                kind=GardenProposalClosureKind.ACCEPTED, item_outcome=GardenProposalItemOutcome.DECLINED
            )
        }
    )
    repo = _RecordingWriteRepo()
    resolution = _resolution(closures=closures, proposals=_FakeProposals(), findings=_FakeFindings(), repo=repo)

    resolution.resolve_for_item(_POINTER)

    assert repo.batches == []


def test_an_accepted_minted_closure_naming_an_absent_proposal_resolves_nothing() -> None:
    closures = _FakeClosures(
        by_item={
            (_POINTER.source, _POINTER.ref): _closure(
                kind=GardenProposalClosureKind.ACCEPTED, item_outcome=GardenProposalItemOutcome.MINTED
            )
        }
    )
    repo = _RecordingWriteRepo()
    resolution = _resolution(closures=closures, proposals=_FakeProposals(), findings=_FakeFindings(), repo=repo)

    resolution.resolve_for_item(_POINTER)

    assert repo.batches == []


def test_an_already_exited_finding_is_skipped_not_re_resolved() -> None:
    closures = _FakeClosures(
        by_item={
            (_POINTER.source, _POINTER.ref): _closure(
                kind=GardenProposalClosureKind.ACCEPTED, item_outcome=GardenProposalItemOutcome.MINTED
            )
        }
    )
    proposals = _FakeProposals(by_id={"gprop_1": _proposal(findings=["fin_1"])})
    findings = _FakeFindings(by_id={"fin_1": _finding("fin_1", state="resolved")})
    repo = _RecordingWriteRepo()
    resolution = _resolution(closures=closures, proposals=proposals, findings=findings, repo=repo)

    resolution.resolve_for_item(_POINTER)

    assert repo.batches == []


def test_resolves_exactly_the_proposals_live_findings_attributed_to_it() -> None:
    closures = _FakeClosures(
        by_item={
            (_POINTER.source, _POINTER.ref): _closure(
                kind=GardenProposalClosureKind.ACCEPTED, item_outcome=GardenProposalItemOutcome.MINTED
            )
        }
    )
    proposals = _FakeProposals(by_id={"gprop_1": _proposal(findings=["fin_1", "fin_2", "fin_3"])})
    findings = _FakeFindings(
        by_id={
            "fin_1": _finding("fin_1", state="live"),
            "fin_2": _finding("fin_2", state="resolved"),  # already exited — untouched
            "fin_3": _finding("fin_3", state="live"),
        }
    )
    repo = _RecordingWriteRepo()
    resolution = _resolution(closures=closures, proposals=proposals, findings=findings, repo=repo)

    resolution.resolve_for_item(_POINTER)

    assert len(repo.batches) == 1
    entries = repo.batches[0]
    assert {e.finding_id for e in entries} == {"fin_1", "fin_3"}
    for entry in entries:
        assert entry.kind == "resolved"
        assert entry.actor == "u_1"
        assert entry.proposal_id == "gprop_1"
        assert entry.note


def test_a_proposal_already_resolved_once_is_never_resolved_again_even_after_a_reopen() -> None:
    """blizzard#394 review F1/F13: a person's `reopened` fact folds a resolved finding
    back to `live` (`derive_liveness`) — a stray repeat call must not read that as "still
    unresolved" and silently redo what the person undid. Gating on
    `has_resolution_for_proposal` rather than each finding's own state closes that hole."""
    closures = _FakeClosures(
        by_item={
            (_POINTER.source, _POINTER.ref): _closure(
                kind=GardenProposalClosureKind.ACCEPTED, item_outcome=GardenProposalItemOutcome.MINTED
            )
        }
    )
    proposals = _FakeProposals(by_id={"gprop_1": _proposal(findings=["fin_1"])})
    # Reopened after an earlier resolution: `state` reads "live" again, exactly like a
    # never-yet-resolved finding — only the proposal-level marker tells them apart.
    findings = _FakeFindings(
        by_id={"fin_1": _finding("fin_1", state="live")}, resolved_proposal_ids=frozenset({"gprop_1"})
    )
    repo = _RecordingWriteRepo()
    resolution = _resolution(closures=closures, proposals=proposals, findings=findings, repo=repo)

    resolution.resolve_for_item(_POINTER)

    assert repo.batches == []


def _answered_findings_resolution(
    *, closures: _FakeClosures, proposals: _FakeProposals, findings: _FakeFindings
) -> AnsweredFindingsReader:
    return AnsweredFindingsReader(closures=closures, proposals=proposals, findings=findings)


def test_a_chunk_with_no_work_refs_resolves_none() -> None:
    resolution = _answered_findings_resolution(
        closures=_FakeClosures(), proposals=_FakeProposals(), findings=_FakeFindings()
    )

    assert resolution.resolve_for_chunk(_chunk(work_refs=[])) is None


def test_a_chunk_whose_item_names_no_closure_resolves_none() -> None:
    resolution = _answered_findings_resolution(
        closures=_FakeClosures(), proposals=_FakeProposals(), findings=_FakeFindings()
    )

    assert resolution.resolve_for_chunk(_chunk()) is None


def test_a_chunk_whose_item_was_minted_by_a_passed_proposal_resolves_none() -> None:
    closures = _FakeClosures(
        by_item={
            (_POINTER.source, _POINTER.ref): GardenProposalClosure(
                proposal_id="gprop_1",
                closure=GardenProposalClosureKind.PASSED,
                reason="not worth it",
                closed_by="u_1",
                closed_at=_T0,
                item_outcome=None,
                source=_POINTER.source,
                ref=_POINTER.ref,
            )
        }
    )
    resolution = _answered_findings_resolution(closures=closures, proposals=_FakeProposals(), findings=_FakeFindings())

    assert resolution.resolve_for_chunk(_chunk()) is None


def test_a_chunk_whose_accept_declined_to_mint_resolves_none() -> None:
    closures = _FakeClosures(
        by_item={
            (_POINTER.source, _POINTER.ref): _closure(
                kind=GardenProposalClosureKind.ACCEPTED, item_outcome=GardenProposalItemOutcome.DECLINED
            )
        }
    )
    resolution = _answered_findings_resolution(closures=closures, proposals=_FakeProposals(), findings=_FakeFindings())

    assert resolution.resolve_for_chunk(_chunk()) is None


def test_a_chunk_naming_a_missing_proposal_resolves_none() -> None:
    closures = _FakeClosures(
        by_item={
            (_POINTER.source, _POINTER.ref): _closure(
                kind=GardenProposalClosureKind.ACCEPTED, item_outcome=GardenProposalItemOutcome.MINTED
            )
        }
    )
    resolution = _answered_findings_resolution(closures=closures, proposals=_FakeProposals(), findings=_FakeFindings())

    assert resolution.resolve_for_chunk(_chunk()) is None


def test_a_minted_chunk_resolves_its_proposals_findings_in_order() -> None:
    closures = _FakeClosures(
        by_item={
            (_POINTER.source, _POINTER.ref): _closure(
                kind=GardenProposalClosureKind.ACCEPTED, item_outcome=GardenProposalItemOutcome.MINTED
            )
        }
    )
    proposals = _FakeProposals(by_id={"gprop_1": _proposal(findings=["fin_2", "fin_1"])})
    findings = _FakeFindings(
        by_id={"fin_1": _finding("fin_1", state="live"), "fin_2": _finding("fin_2", state="resolved")}
    )
    resolution = _answered_findings_resolution(closures=closures, proposals=proposals, findings=findings)

    resolved = resolution.resolve_for_chunk(_chunk())

    assert resolved is not None
    assert [f.finding_id for f in resolved] == ["fin_2", "fin_1"]


def test_a_proposal_finding_id_that_no_longer_resolves_is_skipped() -> None:
    closures = _FakeClosures(
        by_item={
            (_POINTER.source, _POINTER.ref): _closure(
                kind=GardenProposalClosureKind.ACCEPTED, item_outcome=GardenProposalItemOutcome.MINTED
            )
        }
    )
    proposals = _FakeProposals(by_id={"gprop_1": _proposal(findings=["fin_1", "fin_missing"])})
    findings = _FakeFindings(by_id={"fin_1": _finding("fin_1")})
    resolution = _answered_findings_resolution(closures=closures, proposals=proposals, findings=findings)

    resolved = resolution.resolve_for_chunk(_chunk())

    assert resolved is not None
    assert [f.finding_id for f in resolved] == ["fin_1"]
