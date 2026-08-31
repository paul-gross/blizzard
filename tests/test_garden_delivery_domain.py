"""``garden_delivery`` (unit tier, blizzard#393 Phase 2) — the delivery node's own check:
each artifact parses and matches its wire shape, every finding id named is well-formed
and live on the run's routine, a transformation stays inside the run's declared scope,
a cited commit is well-formed and (when addressable) resolves, and a `gone` fact
carries a note. Plain unit tests over hand-built objects — this module takes no I/O
(``bzh:domain-takes-objects``, the ``tests/test_garden_proposals_domain.py`` shape)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from blizzard.foundation.ids import FINDING_PREFIX, Id
from blizzard.hub.domain.garden_delivery import (
    CommitResolver,
    GardenDeliveryRejected,
    check_delta,
    check_proposal,
    parse_delta,
    parse_proposals,
    validate_delivery,
)
from blizzard.hub.domain.run_context import RunContext
from blizzard.wire.finding import AddFindingOp, FindingDelta, GoneFindingOp, ObservedFindingOp
from blizzard.wire.garden_proposal import GardenProposalCandidate

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_RUN = RunContext(routine_name="nightly", scope_slug="runner", mode="full")


def _fin() -> str:
    return Id.mint_at(FINDING_PREFIX, _T0).value


_FIN1 = _fin()
_FIN2 = _fin()
_FIN3 = _fin()

_GOOD_COMMIT = "a" * 40
_BAD_COMMIT = "not-a-sha"


def _live(*, in_scope: bool = True) -> dict[str, str]:
    scope = _RUN.scope_slug if in_scope else "other-scope"
    return {_FIN1: scope, _FIN2: scope, _FIN3: scope}


def _resolver(result: bool | None) -> CommitResolver:
    return lambda repo, sha: result


def _add(*, locus: str = "a.py:1", summary: str = "s", introduced: str | None = None) -> AddFindingOp:
    # `class` is a pydantic alias (`AddFindingOp.class_`) — constructed via
    # `model_validate` rather than the `class_=` kwarg, the `tests/test_finding_wire.py`
    # shape, since pyright resolves the generated `__init__` by alias.
    payload: dict[str, object] = {"op": "add", "class": "stale-docstring", "locus": locus, "summary": summary}
    if introduced is not None:
        payload["introduced"] = introduced
    return AddFindingOp.model_validate(payload)


def _proposal(*, findings: list[str]) -> GardenProposalCandidate:
    return GardenProposalCandidate.model_validate(
        {"ref": "p1", "class": "remediate", "title": "t", "body": "b", "findings": findings}
    )


# --- parse_delta / parse_proposals ------------------------------------------------


def test_parse_delta_rejects_malformed_json() -> None:
    with pytest.raises(GardenDeliveryRejected, match=re.escape("survey.json")):
        parse_delta("survey.json", "{not json")


def test_parse_delta_rejects_a_shape_mismatch() -> None:
    with pytest.raises(GardenDeliveryRejected, match=re.escape("survey.json")):
        parse_delta("survey.json", '{"revisions": {}}')  # missing required `scope`


def test_parse_delta_accepts_a_well_formed_delta() -> None:
    delta = parse_delta("survey.json", '{"scope": "runner", "revisions": {}, "findings": []}')

    assert delta.scope == "runner"
    assert delta.findings == []


def test_parse_proposals_rejects_malformed_json() -> None:
    with pytest.raises(GardenDeliveryRejected, match=re.escape("proposals.json")):
        parse_proposals("proposals.json", "[not json]")


def test_parse_proposals_rejects_a_shape_mismatch() -> None:
    # `findings` is required and non-empty (D7) — an empty list fails the wire shape.
    body = '[{"ref": "p1", "class": "remediate", "title": "t", "body": "b", "findings": []}]'
    with pytest.raises(GardenDeliveryRejected, match=re.escape("proposals.json")):
        parse_proposals("proposals.json", body)


def test_parse_proposals_accepts_well_formed_candidates() -> None:
    body = f'[{{"ref": "p1", "class": "remediate", "title": "t", "body": "b", "findings": ["{_FIN1}"]}}]'

    candidates = parse_proposals("proposals.json", body)

    assert [c.ref for c in candidates] == ["p1"]


# --- finding id well-formedness and liveness --------------------------------------


def test_check_delta_rejects_a_finding_id_that_is_not_fin_ulid_shaped() -> None:
    delta = FindingDelta(scope="runner", findings=[ObservedFindingOp(id="fin_tooshort")])

    with pytest.raises(GardenDeliveryRejected, match="not a well-formed"):
        check_delta(delta, run=_RUN, live_findings=_live())


def test_check_delta_rejects_a_finding_id_not_live_on_this_routine() -> None:
    unknown = _fin()
    delta = FindingDelta(scope="runner", findings=[ObservedFindingOp(id=unknown)])

    with pytest.raises(GardenDeliveryRejected, match="not live on routine"):
        check_delta(delta, run=_RUN, live_findings=_live())


def test_check_delta_rejects_a_transformation_outside_the_declared_scope() -> None:
    delta = FindingDelta(scope="runner", findings=[ObservedFindingOp(id=_FIN1)])

    with pytest.raises(GardenDeliveryRejected, match=f"{_FIN1}.*runner") as exc_info:
        check_delta(delta, run=_RUN, live_findings=_live(in_scope=False))

    assert _FIN1 in str(exc_info.value)
    assert "runner" in str(exc_info.value)


def test_check_proposal_rejects_a_finding_not_live_on_this_routine() -> None:
    proposal = _proposal(findings=[_fin()])

    with pytest.raises(GardenDeliveryRejected, match="not live on routine"):
        check_proposal(proposal, run=_RUN, live_findings=_live())


def test_check_proposal_accepts_a_live_finding() -> None:
    proposal = _proposal(findings=[_FIN1])

    check_proposal(proposal, run=_RUN, live_findings=_live())  # does not raise


# --- commit checks -----------------------------------------------------------------


def test_check_delta_rejects_a_malformed_commit_regardless_of_resolver() -> None:
    delta = FindingDelta(scope="runner", revisions={"blizzard": _BAD_COMMIT})

    with pytest.raises(GardenDeliveryRejected, match="well-formed commit"):
        check_delta(delta, run=_RUN, live_findings=_live(), resolve_commit=_resolver(True))


def test_check_delta_rejects_an_unresolvable_commit_on_an_addressable_repo() -> None:
    delta = FindingDelta(scope="runner", revisions={"blizzard": _GOOD_COMMIT})

    with pytest.raises(GardenDeliveryRejected, match="does not resolve"):
        check_delta(delta, run=_RUN, live_findings=_live(), resolve_commit=_resolver(False))


def test_check_delta_accepts_a_well_formed_resolvable_commit() -> None:
    delta = FindingDelta(scope="runner", revisions={"blizzard": _GOOD_COMMIT})

    check_delta(delta, run=_RUN, live_findings=_live(), resolve_commit=_resolver(True))  # does not raise


def test_check_delta_degrades_to_well_formedness_when_repo_is_unaddressable() -> None:
    delta = FindingDelta(scope="runner", revisions={"blizzard": _GOOD_COMMIT})

    check_delta(delta, run=_RUN, live_findings=_live(), resolve_commit=_resolver(None))  # does not raise


def test_check_delta_degrades_to_well_formedness_when_no_resolver_is_given() -> None:
    delta = FindingDelta(scope="runner", revisions={"blizzard": _GOOD_COMMIT})

    check_delta(delta, run=_RUN, live_findings=_live(), resolve_commit=None)  # does not raise


def test_check_delta_resolves_an_add_ops_introduced_commit_against_the_sole_repo() -> None:
    delta = FindingDelta(
        scope="runner",
        revisions={"blizzard": _GOOD_COMMIT},
        findings=[_add(introduced=_BAD_COMMIT)],
    )

    with pytest.raises(GardenDeliveryRejected, match="well-formed commit"):
        check_delta(delta, run=_RUN, live_findings=_live(), resolve_commit=_resolver(True))


# --- gone / observed fact shape -----------------------------------------------------


def test_check_delta_rejects_a_gone_op_without_a_note() -> None:
    delta = FindingDelta(scope="runner", findings=[GoneFindingOp(id=_FIN1, note="")])

    with pytest.raises(GardenDeliveryRejected, match="non-empty note"):
        check_delta(delta, run=_RUN, live_findings=_live())


def test_check_delta_accepts_a_gone_op_with_a_note() -> None:
    delta = FindingDelta(scope="runner", findings=[GoneFindingOp(id=_FIN1, note="no longer reproduces")])

    check_delta(delta, run=_RUN, live_findings=_live())  # does not raise


def test_check_delta_accepts_an_observed_op_carrying_only_an_id() -> None:
    delta = FindingDelta(scope="runner", findings=[ObservedFindingOp(id=_FIN1)])

    check_delta(delta, run=_RUN, live_findings=_live())  # does not raise


# --- delta shapes --------------------------------------------------------------------


def test_check_delta_accepts_a_clean_delta_with_no_findings() -> None:
    delta = FindingDelta(scope="runner", revisions={"blizzard": _GOOD_COMMIT}, measurement="12 findings")

    check_delta(delta, run=_RUN, live_findings=_live())  # does not raise


def test_check_delta_accepts_an_additions_only_delta() -> None:
    delta = FindingDelta(
        scope="runner",
        findings=[_add(locus="a.py:1", summary="s"), _add(locus="b.py:2", summary="s2")],
    )

    check_delta(delta, run=_RUN, live_findings=_live())  # does not raise


def test_check_delta_accepts_a_transformations_only_delta() -> None:
    delta = FindingDelta(
        scope="runner",
        findings=[ObservedFindingOp(id=_FIN1), GoneFindingOp(id=_FIN2, note="fixed upstream")],
    )

    check_delta(delta, run=_RUN, live_findings=_live())  # does not raise


def test_check_delta_accepts_a_mixed_delta() -> None:
    delta = FindingDelta(
        scope="runner",
        findings=[_add(), ObservedFindingOp(id=_FIN1), GoneFindingOp(id=_FIN2, note="fixed upstream")],
    )

    check_delta(delta, run=_RUN, live_findings=_live())  # does not raise


# --- validate_delivery, end to end ---------------------------------------------------


def test_validate_delivery_accepts_a_full_delivery_and_bundles_it() -> None:
    delta = FindingDelta(scope="runner", findings=[ObservedFindingOp(id=_FIN1)])
    proposal = _proposal(findings=[_FIN1])

    result = validate_delivery(
        run=_RUN,
        delta_artifacts={"survey.json": delta.model_dump_json(by_alias=True)},
        proposal_artifacts={"proposals.json": f"[{proposal.model_dump_json(by_alias=True)}]"},
        live_findings=_live(),
    )

    assert result.run == _RUN
    assert result.deltas == [delta]
    assert [p.ref for p in result.proposals] == ["p1"]


def test_validate_delivery_rejects_on_the_first_failing_artifact() -> None:
    delta = FindingDelta(scope="runner", findings=[ObservedFindingOp(id=_fin())])  # unknown finding

    with pytest.raises(GardenDeliveryRejected, match="not live on routine"):
        validate_delivery(
            run=_RUN,
            delta_artifacts={"survey.json": delta.model_dump_json(by_alias=True)},
            proposal_artifacts={},
            live_findings=_live(),
        )
