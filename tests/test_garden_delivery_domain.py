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
from blizzard.hub.domain.findings import Finding
from blizzard.hub.domain.garden_delivery import (
    CommitResolution,
    CommitResolver,
    GardenDeliveryRejected,
    check_add_refs,
    check_delta,
    check_proposal,
    check_proposal_refs,
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


def _finding(
    finding_id: str, *, scope_slug: str = _RUN.scope_slug, live: bool = True, state: str | None = None
) -> Finding:
    resolved_state = state if state is not None else ("live" if live else "gone")
    return Finding(
        finding_id=finding_id,
        routine_name=_RUN.routine_name,
        scope_slug=scope_slug,
        class_="stale-docstring",
        locus="a.py:1",
        summary="s",
        introduced=None,
        introduced_at=None,
        first_observed_at=_T0,
        live=live,
        state=resolved_state,
        note=None,
        last_seen_at=_T0,
        observed_count=1,
    )


def _resolver(result: bool | None) -> CommitResolver:
    resolution = None if result is None else CommitResolution(exists=result)
    return lambda repo, sha: resolution


def _add(
    *, locus: str = "a.py:1", summary: str = "s", introduced: str | None = None, ref: str | None = None
) -> AddFindingOp:
    # `class` is a pydantic alias (`AddFindingOp.class_`), so this builds via
    # `model_validate` rather than the `class_=` kwarg — `tests/test_finding_wire.py`'s shape.
    payload: dict[str, object] = {"op": "add", "class": "stale-docstring", "locus": locus, "summary": summary}
    if introduced is not None:
        payload["introduced"] = introduced
    if ref is not None:
        payload["ref"] = ref
    return AddFindingOp.model_validate(payload)


def _proposal(*, findings: list[str], ref: str = "p1") -> GardenProposalCandidate:
    return GardenProposalCandidate.model_validate(
        {"ref": ref, "class": "remediate", "title": "t", "body": "b", "findings": findings}
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


def test_check_delta_rejects_a_delta_whose_scope_differs_from_the_runs_declared_scope() -> None:
    delta = FindingDelta(scope="other-scope", findings=[])

    with pytest.raises(GardenDeliveryRejected, match=r"other-scope.*runner") as exc_info:
        check_delta(delta, run=_RUN, live_findings=_live())

    assert "other-scope" in str(exc_info.value)
    assert "runner" in str(exc_info.value)


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


def test_check_proposal_rejects_the_same_finding_id_cited_twice() -> None:
    # `garden_proposal_findings`'s primary key is `(proposal_id, finding_id)` — a
    # repeated citation would collide there rather than bounce legibly.
    proposal = _proposal(findings=[_FIN1, _FIN1])

    with pytest.raises(GardenDeliveryRejected, match=f"{_FIN1}.*more than once"):
        check_proposal(proposal, run=_RUN, live_findings=_live())


def test_check_proposal_rejects_the_same_ref_cited_twice() -> None:
    proposal = _proposal(findings=["new-1", "new-1"])

    with pytest.raises(GardenDeliveryRejected, match=r"new-1.*more than once"):
        check_proposal(proposal, run=_RUN, live_findings=_live(), known_refs=frozenset({"new-1"}))


def test_check_proposal_refs_rejects_one_artifact_naming_a_ref_twice() -> None:
    # `(source artifact, ref)` is a delivered proposal's identity — one artifact naming
    # `p1` twice claims two different proposals are the same one.
    proposals = [_proposal(findings=[_FIN1]), _proposal(findings=[_FIN2])]

    with pytest.raises(GardenDeliveryRejected, match="more than once"):
        check_proposal_refs("docket", proposals)


def test_check_proposal_refs_accepts_distinct_refs() -> None:
    proposals = [_proposal(findings=[_FIN1], ref="p1"), _proposal(findings=[_FIN2], ref="p2")]

    check_proposal_refs("docket", proposals)  # does not raise


# --- submission-local finding refs --------------------------------------------------


def test_check_delta_rejects_an_add_ops_ref_shaped_like_a_finding_id() -> None:
    delta = FindingDelta(scope="runner", findings=[_add(ref=_fin())])

    with pytest.raises(GardenDeliveryRejected, match="shaped like a finding id"):
        check_delta(delta, run=_RUN, live_findings=_live())


def test_check_delta_accepts_an_add_op_carrying_an_ordinary_ref() -> None:
    delta = FindingDelta(scope="runner", findings=[_add(ref="new-1")])

    check_delta(delta, run=_RUN, live_findings=_live())  # does not raise


def test_check_add_refs_collects_every_distinct_ref_across_deltas() -> None:
    delta_a = FindingDelta(scope="runner", findings=[_add(locus="a.py:1", ref="new-1")])
    delta_b = FindingDelta(scope="runner", findings=[_add(locus="b.py:2", ref="new-2")])

    assert check_add_refs([delta_a, delta_b]) == {"new-1", "new-2"}


def test_check_add_refs_ignores_add_ops_carrying_no_ref() -> None:
    delta = FindingDelta(scope="runner", findings=[_add(locus="a.py:1")])

    assert check_add_refs([delta]) == frozenset()


def test_check_add_refs_rejects_a_ref_duplicated_across_deltas() -> None:
    delta_a = FindingDelta(scope="runner", findings=[_add(locus="a.py:1", ref="new-1")])
    delta_b = FindingDelta(scope="runner", findings=[_add(locus="b.py:2", ref="new-1")])

    with pytest.raises(GardenDeliveryRejected, match=r"new-1.*more than once"):
        check_add_refs([delta_a, delta_b])


def test_check_proposal_accepts_a_ref_from_this_deliverys_own_deltas() -> None:
    proposal = _proposal(findings=["new-1"])

    check_proposal(proposal, run=_RUN, live_findings=_live(), known_refs=frozenset({"new-1"}))  # does not raise


def test_check_proposal_rejects_a_ref_absent_from_this_deliverys_deltas() -> None:
    proposal = _proposal(findings=["ghost-ref"])

    with pytest.raises(GardenDeliveryRejected, match="ghost-ref"):
        check_proposal(proposal, run=_RUN, live_findings=_live(), known_refs=frozenset({"new-1"}))


def test_check_proposal_accepts_a_mix_of_live_id_and_own_run_ref() -> None:
    proposal = _proposal(findings=[_FIN1, "new-1"])

    check_proposal(proposal, run=_RUN, live_findings=_live(), known_refs=frozenset({"new-1"}))  # does not raise


def test_validate_delivery_resolves_a_proposal_citing_its_own_runs_add_ref() -> None:
    delta = FindingDelta(scope="runner", findings=[_add(ref="new-1")])
    proposal = _proposal(findings=["new-1"])

    result = validate_delivery(
        run=_RUN,
        delta_artifacts={"survey.json": delta.model_dump_json(by_alias=True)},
        proposal_artifacts={"docket": f"[{proposal.model_dump_json(by_alias=True)}]"},
        known_findings=[],
    )

    assert [p.findings for p in result.proposals] == [["new-1"]]


def test_validate_delivery_rejects_a_proposal_citing_a_ref_absent_from_the_delta() -> None:
    proposal = _proposal(findings=["ghost-ref"])

    with pytest.raises(GardenDeliveryRejected, match="ghost-ref"):
        validate_delivery(
            run=_RUN,
            delta_artifacts={},
            proposal_artifacts={"docket": f"[{proposal.model_dump_json(by_alias=True)}]"},
            known_findings=[],
        )


def test_validate_delivery_rejects_an_add_ref_duplicated_across_two_delta_artifacts() -> None:
    delta_a = FindingDelta(scope="runner", findings=[_add(locus="a.py:1", ref="new-1")])
    delta_b = FindingDelta(scope="runner", findings=[_add(locus="b.py:2", ref="new-1")])

    with pytest.raises(GardenDeliveryRejected, match=r"new-1.*more than once"):
        validate_delivery(
            run=_RUN,
            delta_artifacts={
                "survey-a.json": delta_a.model_dump_json(by_alias=True),
                "survey-b.json": delta_b.model_dump_json(by_alias=True),
            },
            proposal_artifacts={},
            known_findings=[],
        )


def test_validate_delivery_still_accepts_a_proposal_citing_a_prior_runs_live_id() -> None:
    """A docket citing a `fin_` id already live on the routine still resolves exactly as
    before this submission-local ref existed — unaffected by whether this delivery's own
    deltas carry any `add` ops at all."""
    proposal = _proposal(findings=[_FIN1])

    result = validate_delivery(
        run=_RUN,
        delta_artifacts={},
        proposal_artifacts={"docket": f"[{proposal.model_dump_json(by_alias=True)}]"},
        known_findings=[_finding(_FIN1)],
    )

    assert [p.findings for p in result.proposals] == [[_FIN1]]


def test_validate_delivery_accepts_an_empty_proposals_docket() -> None:
    """`--proposals` naming an artifact whose content is `[]` — a docket with nothing in
    it — validates cleanly, `validate_delivery`'s own share of "an empty docket delivers
    as recorded"."""
    delta = FindingDelta(scope="runner", findings=[])

    result = validate_delivery(
        run=_RUN,
        delta_artifacts={"survey.json": delta.model_dump_json(by_alias=True)},
        proposal_artifacts={"docket": "[]"},
        known_findings=[],
    )

    assert result.proposals == []


# --- commit checks -----------------------------------------------------------------


def test_check_delta_rejects_a_commit_sha_with_a_trailing_newline() -> None:
    delta = FindingDelta(scope="runner", revisions={"blizzard": "abcdef1\n"})

    with pytest.raises(GardenDeliveryRejected, match="well-formed commit"):
        check_delta(delta, run=_RUN, live_findings=_live(), resolve_commit=_resolver(True))


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
    # Well-formed but unresolvable, so it reaches the resolver — proving the sole-repo
    # branch runs at all, not merely that malformed shas are rejected before it.
    other_commit = "b" * 40
    calls: list[tuple[str, str]] = []

    def _stub(repo: str, sha: str) -> CommitResolution:
        calls.append((repo, sha))
        return CommitResolution(exists=sha != other_commit)

    delta = FindingDelta(
        scope="runner",
        revisions={"blizzard": _GOOD_COMMIT},
        findings=[_add(introduced=other_commit)],
    )

    with pytest.raises(GardenDeliveryRejected, match="does not resolve"):
        check_delta(delta, run=_RUN, live_findings=_live(), resolve_commit=_stub)

    assert ("blizzard", other_commit) in calls


def test_check_delta_returns_the_resolved_introduced_at_for_a_sole_repo() -> None:
    commit_at = datetime(2025, 12, 25, tzinfo=UTC)

    def _stub(repo: str, sha: str) -> CommitResolution:
        return CommitResolution(exists=True, authored_at=commit_at)

    delta = FindingDelta(scope="runner", revisions={"blizzard": _GOOD_COMMIT}, findings=[_add(introduced=_GOOD_COMMIT)])

    introduced_at = check_delta(delta, run=_RUN, live_findings=_live(), resolve_commit=_stub)

    assert introduced_at == {("blizzard", _GOOD_COMMIT): commit_at}


def test_check_delta_returns_no_introduced_at_when_the_repo_count_is_ambiguous() -> None:
    delta = FindingDelta(
        scope="runner",
        revisions={"blizzard": _GOOD_COMMIT, "blizzard-context": "c" * 40},
        findings=[_add(introduced=_GOOD_COMMIT)],
    )

    introduced_at = check_delta(delta, run=_RUN, live_findings=_live(), resolve_commit=_resolver(True))

    assert introduced_at == {}


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
        known_findings=[_finding(_FIN1), _finding(_FIN2), _finding(_FIN3)],
    )

    assert result.run == _RUN
    assert result.deltas == [delta]
    assert [p.ref for p in result.proposals] == ["p1"]
    assert result.proposal_sources == ["proposals.json"]


def test_validate_delivery_pairs_each_proposal_with_its_own_source_artifact_name() -> None:
    """`proposal_sources` stays positionally parallel to `proposals` across several
    artifacts, each carrying several candidates — the materialize phase's `zip(...,
    strict=True)` depends on this holding exactly."""
    proposal_a1 = _proposal(findings=[_FIN1], ref="a1")
    proposal_a2 = _proposal(findings=[_FIN1], ref="a2")
    proposal_b1 = _proposal(findings=[_FIN2], ref="b1")

    result = validate_delivery(
        run=_RUN,
        delta_artifacts={},
        proposal_artifacts={
            "docket-a": f"[{proposal_a1.model_dump_json(by_alias=True)}, {proposal_a2.model_dump_json(by_alias=True)}]",
            "docket-b": f"[{proposal_b1.model_dump_json(by_alias=True)}]",
        },
        known_findings=[_finding(_FIN1), _finding(_FIN2)],
    )

    assert [p.ref for p in result.proposals] == ["a1", "a2", "b1"]
    assert result.proposal_sources == ["docket-a", "docket-a", "docket-b"]


def test_validate_delivery_accepts_two_artifacts_reusing_one_ref() -> None:
    """A `ref` is stable only within its own submission, so two artifacts each naming
    `p1` carry unrelated proposals — distinct under `(source artifact, ref)`."""
    proposal = _proposal(findings=[_FIN1])
    raw = f"[{proposal.model_dump_json(by_alias=True)}]"

    result = validate_delivery(
        run=_RUN,
        delta_artifacts={},
        proposal_artifacts={"docket-a": raw, "docket-b": raw},
        known_findings=[_finding(_FIN1)],
    )

    assert [p.ref for p in result.proposals] == ["p1", "p1"]
    assert result.proposal_sources == ["docket-a", "docket-b"]


def test_validate_delivery_rejects_one_artifact_naming_a_ref_twice() -> None:
    proposal = _proposal(findings=[_FIN1])
    twice = f"[{proposal.model_dump_json(by_alias=True)}, {proposal.model_dump_json(by_alias=True)}]"

    with pytest.raises(GardenDeliveryRejected, match="more than once"):
        validate_delivery(
            run=_RUN,
            delta_artifacts={},
            proposal_artifacts={"docket": twice},
            known_findings=[_finding(_FIN1)],
        )


def test_validate_delivery_accepts_an_observed_op_reviving_a_gone_finding() -> None:
    # A finding recorded `gone` must still be present in `known_findings` (D3's
    # reversibility) — an `observed` targeting it is accepted, not rejected as unknown.
    delta = FindingDelta(scope="runner", findings=[ObservedFindingOp(id=_FIN1)])

    result = validate_delivery(
        run=_RUN,
        delta_artifacts={"survey.json": delta.model_dump_json(by_alias=True)},
        proposal_artifacts={},
        known_findings=[_finding(_FIN1, live=False)],
    )

    assert result.deltas == [delta]


def test_validate_delivery_rejects_an_op_naming_an_exited_finding() -> None:
    """A human-exited finding is dropped from `live_findings` (blizzard#394 D3) — unlike
    `gone`, an exit is not addressable by a later run's delta op."""
    delta = FindingDelta(scope="runner", findings=[ObservedFindingOp(id=_FIN1)])

    with pytest.raises(GardenDeliveryRejected, match="has been exited"):
        validate_delivery(
            run=_RUN,
            delta_artifacts={"survey.json": delta.model_dump_json(by_alias=True)},
            proposal_artifacts={},
            known_findings=[_finding(_FIN1, live=False, state="resolved")],
        )


def test_validate_delivery_resolves_each_distinct_commit_at_most_once() -> None:
    """The resolver is a network call holding the fleet-wide hub-exec slot, so a delta
    citing one commit across many findings must spend exactly one call on it."""
    calls: list[tuple[str, str]] = []

    def _stub(repo: str, sha: str) -> CommitResolution:
        calls.append((repo, sha))
        return CommitResolution(exists=True)

    shared = "c" * 40
    delta = FindingDelta(
        scope="runner",
        revisions={"blizzard": _GOOD_COMMIT},
        findings=[_add(locus=f"a.py:{i}", introduced=shared) for i in range(5)],
    )

    validate_delivery(
        run=_RUN,
        delta_artifacts={"survey.json": delta.model_dump_json(by_alias=True)},
        proposal_artifacts={},
        known_findings=[],
        resolve_commit=_stub,
    )

    assert sorted(calls) == [("blizzard", _GOOD_COMMIT), ("blizzard", shared)]


def test_validate_delivery_rejects_on_the_first_failing_artifact() -> None:
    delta = FindingDelta(scope="runner", findings=[ObservedFindingOp(id=_fin())])  # unknown finding

    with pytest.raises(GardenDeliveryRejected, match="not live on routine"):
        validate_delivery(
            run=_RUN,
            delta_artifacts={"survey.json": delta.model_dump_json(by_alias=True)},
            proposal_artifacts={},
            known_findings=[_finding(_FIN1), _finding(_FIN2), _finding(_FIN3)],
        )
