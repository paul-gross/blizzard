"""``FindingExitService`` (unit tier, blizzard#394 Phase 1): a blank or missing note is
refused before the repository is ever touched, every verb records the right fact kind,
`resolve` and `supersede` alone carry their extra field, and a multi-finding call is one
`record_facts` batch — the `tests/test_garden_proposal_closure_domain.py` shape."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.findings import (
    FactEntry,
    Finding,
    FindingExitService,
    FindingNoteRequiredError,
    IWriteFindingRepository,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _finding(finding_id: str) -> Finding:
    return Finding(
        finding_id=finding_id,
        routine_name="nightly",
        scope_slug="runner",
        class_="stale-docstring",
        locus="src/a.py:1",
        summary="s",
        introduced=None,
        introduced_at=None,
        live=True,
        state="live",
        note=None,
        last_seen_at=_T0,
        observed_count=0,
    )


@dataclass
class _FakeFindingRepo:
    batches: list[list[FactEntry]] = field(default_factory=list)

    def record_facts(self, entries: list[FactEntry]) -> None:
        self.batches.append(list(entries))


def _as_write_repo(repo: _FakeFindingRepo) -> IWriteFindingRepository:
    return cast(IWriteFindingRepository, repo)


def _service(repo: _FakeFindingRepo) -> FindingExitService:
    return FindingExitService(repo=_as_write_repo(repo), clock=FixedClock(instant=_T0))


def test_resolve_rejects_a_blank_note() -> None:
    repo = _FakeFindingRepo()
    service = _service(repo)

    with pytest.raises(FindingNoteRequiredError):
        service.resolve([_finding("fin_1")], note="   ", actor="u1")

    assert repo.batches == []


def test_resolve_rejects_a_missing_note_kind_named_in_the_error() -> None:
    repo = _FakeFindingRepo()
    service = _service(repo)

    with pytest.raises(FindingNoteRequiredError, match="resolved"):
        service.resolve([_finding("fin_1")], note="", actor="u1")


def test_resolve_strips_and_records_one_fact_per_finding_in_one_batch() -> None:
    repo = _FakeFindingRepo()
    service = _service(repo)

    service.resolve([_finding("fin_1"), _finding("fin_2")], note="  fixed upstream  ", actor="u1")

    assert len(repo.batches) == 1
    assert repo.batches[0] == [
        FactEntry(finding_id="fin_1", kind="resolved", at=_T0, note="fixed upstream", actor="u1"),
        FactEntry(finding_id="fin_2", kind="resolved", at=_T0, note="fixed upstream", actor="u1"),
    ]


def test_resolve_carries_a_proposal_id_when_supplied() -> None:
    repo = _FakeFindingRepo()
    service = _service(repo)

    service.resolve([_finding("fin_1")], note="answered", actor="u1", proposal_id="gprop_1")

    assert repo.batches[0] == [
        FactEntry(finding_id="fin_1", kind="resolved", at=_T0, note="answered", actor="u1", proposal_id="gprop_1")
    ]


def test_confirm_gone_records_gone_confirmed() -> None:
    repo = _FakeFindingRepo()
    service = _service(repo)

    service.confirm_gone([_finding("fin_1")], note="checked by hand", actor="u1")

    assert repo.batches[0] == [
        FactEntry(finding_id="fin_1", kind="gone-confirmed", at=_T0, note="checked by hand", actor="u1")
    ]


def test_wont_fix_records_wont_fix() -> None:
    repo = _FakeFindingRepo()
    service = _service(repo)

    service.wont_fix([_finding("fin_1")], note="accepted risk", actor="u1")

    assert repo.batches[0] == [FactEntry(finding_id="fin_1", kind="wont-fix", at=_T0, note="accepted risk", actor="u1")]


def test_not_a_finding_records_not_a_finding() -> None:
    repo = _FakeFindingRepo()
    service = _service(repo)

    service.not_a_finding([_finding("fin_1")], note="false positive", actor="u1")

    assert repo.batches[0] == [
        FactEntry(finding_id="fin_1", kind="not-a-finding", at=_T0, note="false positive", actor="u1")
    ]


def test_supersede_requires_the_absorbing_finding_id() -> None:
    repo = _FakeFindingRepo()
    service = _service(repo)

    service.supersede([_finding("fin_1")], note="folded into fin_2", actor="u1", superseded_by="fin_2")

    assert repo.batches[0] == [
        FactEntry(
            finding_id="fin_1", kind="superseded", at=_T0, note="folded into fin_2", actor="u1", superseded_by="fin_2"
        )
    ]


def test_reopen_records_reopened() -> None:
    repo = _FakeFindingRepo()
    service = _service(repo)

    service.reopen([_finding("fin_1")], note="regressed", actor="u1")

    assert repo.batches[0] == [FactEntry(finding_id="fin_1", kind="reopened", at=_T0, note="regressed", actor="u1")]


def test_an_empty_finding_list_still_writes_an_empty_batch() -> None:
    repo = _FakeFindingRepo()
    service = _service(repo)

    service.resolve([], note="n/a", actor="u1")

    assert repo.batches == [[]]
