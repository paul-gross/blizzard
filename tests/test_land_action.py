"""``land`` against the in-memory forge double: each outcome its stages answer, the markers they record,
and — through the executor — the landing record a merge writes past the fence (component tier)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

import httpx2
import pytest
from sqlalchemy import insert, select

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.hub_event_types import HubEventType
from blizzard.hub.delivery.actions.land import Land
from blizzard.hub.domain.actions.effects import Effect, EffectResult, EffectStatus, LandingRecord, Marker, Pass
from blizzard.hub.domain.actions.visit import GitCommits, LandingProject, Visit, VisitChunk, VisitNode
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeFields, ForgeId
from blizzard.hub.domain.config.repositories import (
    RepositoryId,
    RepositoryOutcome,
    RepositoryResolution,
    ResolvedRepository,
)
from blizzard.hub.forge.internal.github_landing import GitHubLanding
from blizzard.hub.forge.landing import (
    Change,
    ChangeState,
    Check,
    CheckOutcome,
    CommitSummary,
    ComparisonStatus,
    FileChange,
    ForgeNotFound,
    ForgeObject,
    ForgeRateLimited,
    ForgeRejected,
    ForgeUnavailable,
    Mergeability,
    MergeMethod,
    RepositoryForge,
    Update,
    UpdateMethod,
    UpdateOutcome,
)
from blizzard.hub.forge.registry import ForgeBinding
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.github_double import GREEN, DoubleRepo, GitHubDouble, change, check, comparison
from tests.support import (
    HubHarness,
    RepositorySeed,
    build_hub,
    emitted_events,
    hub_scoped,
    hub_store_connections,
    seed_work_item,
    stamp,
)

pytestmark = pytest.mark.component

_WIDGET = "acme/widget"
_GADGET = "acme/gadget"
_SUBMITTED = "c" * 40


# --- the action, over a recording sink ----------------------------------------


class _Sink:
    """Records every effect; a landing answers ``fenced`` once ``fence_landings`` is set, and a marker whose
    name starts with one of ``refuse`` answers ``fenced``."""

    def __init__(self, *, fence_landings: bool = False, refuse: tuple[str, ...] = ()) -> None:
        self.effects: list[Effect] = []
        self.fence_landings = fence_landings
        self._refuse = refuse

    async def record(self, effect: Effect) -> EffectResult:
        self.effects.append(effect)
        if isinstance(effect, LandingRecord) and self.fence_landings:
            return EffectResult(EffectStatus.FENCED, "the chunk is terminal or has moved past this visit")
        if isinstance(effect, Marker) and effect.name.startswith(self._refuse):
            return EffectResult(EffectStatus.FENCED, "not recorded")
        return EffectResult(EffectStatus.RECORDED)

    def landings(self) -> list[LandingRecord]:
        return [e for e in self.effects if isinstance(e, LandingRecord)]

    def markers(self) -> dict[str, str]:
        return {e.name: e.content for e in self.effects if isinstance(e, Marker)}


def _row(
    kind: ArtifactKind, name: str, data: str, *, repo: str | None = None, node_id: str = "nd_deliver"
) -> StoredArtifact:
    return StoredArtifact(
        kind=kind,
        name=name,
        data=data,
        repo=repo,
        forge=None,
        artifact_id=f"ar_{name}_{repo}",
        chunk_id="ch_1",
        node_id=node_id,
        node_name="deliver",
        epoch=1,
    )


_EXAMPLE_FORGE = ConfiguredForge(
    forge_id=ForgeId("frg_example"),
    slug="example",
    name="example",
    kind="github",
    fields=ForgeFields("https://api.github.example", None),
    revision=1,
    created_at=datetime(2026, 10, 1, tzinfo=UTC),
    created_by="op",
)


def _visit(
    repos: tuple[str, ...] = (_WIDGET,),
    *,
    markers: tuple[str, ...] = (),
    expects: bool = True,
    feature_title: str | None = None,
    work_items: tuple[dict[str, str | None], ...] = (),
    public_url: str | None = None,
) -> Visit:
    commits = [
        _row(ArtifactKind.GIT_COMMIT, f"w{i}", f"feat/x:{_SUBMITTED}", repo=repo, node_id="nd_build")
        for i, repo in enumerate(repos)
    ]
    marker_rows = tuple(_row(ArtifactKind.ASSET, name, "x") for name in markers)
    targets = tuple(
        ResolvedRepository(
            forge_id=_EXAMPLE_FORGE.forge_id,
            owner="acme",
            base_branch="main",
            secret_name="s",
            record=f"re_{repo.partition('/')[2]}",
        )
        for repo in repos
    )
    return Visit(
        chunk=VisitChunk(chunk_id="ch_1", project_id=None, minted_at=datetime(2026, 10, 23, tzinfo=UTC), work_refs=()),
        node=VisitNode(
            node_id="nd_deliver",
            name="deliver",
            action="land",
            action_args=MappingProxyType({}),
            expects_git_commits=expects,
        ),
        epoch=1,
        artifacts=(*commits, *marker_rows),
        markers=marker_rows,
        git_commits=GitCommits.of(commits),
        resolution=RepositoryResolution(
            RepositoryOutcome.RESOLVED if repos else RepositoryOutcome.NOTHING_TO_RESOLVE,
            targets=targets,
            qualified=repos,
            records=tuple(t.record for t in targets),
            forges=(_EXAMPLE_FORGE,),
        ),
        work_items=tuple(MappingProxyType(item) for item in work_items),
        feature_title=feature_title,
        public_url=public_url,
        project=LandingProject(),
        first_turn_at=datetime(2026, 10, 24, tzinfo=UTC),
        now=datetime(2026, 10, 24, tzinfo=UTC),
    )


def _forge(*repos: str, **state: Any) -> GitHubDouble:
    double = GitHubDouble()
    for repo in repos or (_WIDGET,):
        double.repos[repo] = DoubleRepo(**{"heads": {"feat/x": _SUBMITTED}, **state})
    return double


def _land(
    double: GitHubDouble,
    visit: Visit | None = None,
    sink: _Sink | None = None,
    offered: frozenset[Capability] = frozenset(Capability),
) -> tuple[Pass, _Sink]:
    visit = visit or _visit()
    sink = sink or _Sink()
    repos = dict(zip(visit.resolution.records, visit.resolution.qualified, strict=True))
    forges = MappingProxyType({RepositoryId(r): double.forge(r, repo, offered) for r, repo in repos.items()})
    return asyncio.run(Land().take(visit, forges, sink)), sink


def test_pending_landing_polls_use_only_conditional_forge_reads_after_the_first_pass() -> None:
    answers: list[tuple[httpx2.Request, int]] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == f"/repos/{_WIDGET}/pulls":
            if request.url.params["state"] == "closed":
                body: object = []
            else:
                body = [_pending_pull()]
        elif path == f"/repos/{_WIDGET}/pulls/1":
            body = _pending_pull()
        elif path == f"/repos/{_WIDGET}/commits/{_SUBMITTED}/check-runs":
            body = {"check_runs": [{"id": 1, "name": "ci", "status": "in_progress", "conclusion": None}]}
        else:
            raise AssertionError(f"unexpected landing read: {request.url}")
        etag = f'"{request.url}"'
        if request.headers.get("if-none-match") == etag:
            answers.append((request, 304))
            return httpx2.Response(304)
        answers.append((request, 200))
        return httpx2.Response(200, json=body, headers={"ETag": etag})

    binding = ForgeBinding(
        kind="github",
        api_url="https://api.github.example",
        owner="acme",
        repo="widget",
        token="tok",
        write_lock=asyncio.Lock,
        learn=lambda _headers: None,
    )
    forge = GitHubLanding(
        binding, clock=FixedClock(datetime(2026, 10, 23, tzinfo=UTC)), transport=httpx2.MockTransport(answer)
    )
    visit = _visit()
    sink = _Sink()
    forges = MappingProxyType(
        {RepositoryId("re_widget"): RepositoryForge(RepositoryId("re_widget"), forge, forge, forge, forge.OFFERED)}
    )

    async def polls() -> None:
        try:
            assert (await Land().take(visit, forges, sink)).choice == "pending"
            answers.clear()
            for _ in range(10):
                assert (await Land().take(visit, forges, sink)).choice == "pending"
        finally:
            forge.close()
            await asyncio.sleep(0)

    asyncio.run(polls())
    assert len(answers) == 40
    assert all(status == 304 and request.headers.get("if-none-match") for request, status in answers)
    lookups = [request for request, _ in answers if request.url.path.endswith("/pulls")]
    assert len([request for request in lookups if request.url.params["state"] == "closed"]) == 10
    assert all(request.url.params["head"] == "acme:feat/x" for request in lookups)


def _pending_pull() -> dict[str, object]:
    return {
        "number": 1,
        "state": "open",
        "mergeable_state": "blocked",
        "head": {"ref": "feat/x", "sha": _SUBMITTED},
        "base": {"ref": "main", "sha": "base"},
        "html_url": f"https://github.example/{_WIDGET}/pull/1",
    }


def test_a_clean_green_pr_merges_its_current_head_and_hands_over_the_landing() -> None:
    double = _forge()

    answer, sink = _land(double)

    assert answer.choice == "landed"
    ((_, merge),) = double.named("merge")
    assert (merge["expected_head"], merge["method"]) == (_SUBMITTED, MergeMethod.MERGE)
    assert sink.landings() == [LandingRecord(repository="re_widget", repo=_WIDGET, revision=f"merge-{_WIDGET}-1")]
    assert json.loads(sink.markers()[f"delivery-pr/{_WIDGET}/1"]) == {
        "repo": _WIDGET,
        "number": 1,
        "url": f"https://github.example/{_WIDGET}/pull/1",
    }


def test_the_pr_title_body_and_merge_message_carry_the_feature_and_its_work_items() -> None:
    double = _forge()
    visit = _visit(
        feature_title="Add the widget",
        work_items=({"label": "default:7", "reference": "acme/widget#7"}, {"label": "default:8", "reference": None}),
        public_url="https://board.example/",
    )

    _land(double, visit)

    ((_, opened),) = double.named("open_change")
    assert opened["title"] == "Add the widget"
    assert opened["body"] == (
        "Delivered by blizzard.\n\nWork items:\n- Refs acme/widget#7\n- default:8\n\nChunk: ch_1\n\n"
        "Board: https://board.example/board/chunk/ch_1\n"
    )
    assert double.named("merge")[0][1]["message"] == "Add the widget\n\nRefs acme/widget#7\ndefault:8"


def test_without_a_feature_title_the_branch_and_the_land_string_stand_in_and_a_long_title_is_cut() -> None:
    double = _forge()
    _land(double)
    assert double.named("open_change")[0][1]["title"] == "feat/x"
    assert double.named("merge")[0][1]["message"] == f"blizzard: land {_WIDGET}"

    long = _forge()
    _land(long, _visit(feature_title="x" * 300))
    title = long.named("open_change")[0][1]["title"]
    assert len(title) == 256 and title.endswith("…")


def test_a_conflicting_pr_answers_conflict_without_merging() -> None:
    double = _forge(mergeability=Mergeability.CONFLICTING)
    answer, sink = _land(double)
    assert answer.choice == "conflict"
    assert double.named("merge") == [] and sink.landings() == []


def test_a_behind_pr_is_updated_from_its_base_on_its_head_and_waits() -> None:
    double = _forge(mergeability=Mergeability.BEHIND)
    answer, _ = _land(double)
    assert answer.choice == "pending"
    assert double.named("update_from_base")[0][1]["expected_head"] == _SUBMITTED
    assert double.named("merge") == []


def test_an_update_from_base_conflict_answers_conflict() -> None:
    double = _forge(mergeability=Mergeability.BEHIND, update=Update(UpdateOutcome.CONFLICT, UpdateMethod.MERGE))
    answer, _ = _land(double)
    assert answer.choice == "conflict"


@pytest.mark.parametrize(
    "refusal", [Update(UpdateOutcome.REFUSED, UpdateMethod.MERGE), ForgeNotFound(ForgeObject.CHANGE)]
)
def test_an_update_refused_or_of_a_change_the_forge_lost_waits(refusal: object) -> None:
    double = _forge(mergeability=Mergeability.BEHIND, replies={"update_from_base": [refusal]})
    answer, _ = _land(double)
    assert answer.choice == "pending"


@pytest.mark.parametrize("state", [Mergeability.BLOCKED, Mergeability.MERGEABLE])
def test_a_watched_head_with_checks_still_running_waits_and_names_them(state: Mergeability) -> None:
    double = _forge(
        mergeability=state, checks={_SUBMITTED: (check(outcome=CheckOutcome.PENDING, detail="in_progress"),)}
    )
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert double.named("merge") == []
    assert "Still running:\n  - build: in_progress" in sink.markers()["delivery-findings"]


def test_a_blocked_head_with_green_checks_merges() -> None:
    double = _forge(mergeability=Mergeability.BLOCKED)
    answer, _ = _land(double)
    assert answer.choice == "landed"


@pytest.mark.parametrize("state", [Mergeability.PENDING, Mergeability.DRAFT])
def test_an_undecided_or_draft_change_never_reads_checks_nor_writes_findings(state: Mergeability) -> None:
    double = _forge(mergeability=state)
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert double.named("checks") == []
    assert "delivery-findings" not in sink.markers()


def test_an_empty_check_list_waits_without_findings() -> None:
    double = _forge(checks={_SUBMITTED: ()})
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert "delivery-findings" not in sink.markers()


@pytest.mark.parametrize(
    "refusal", [None, ForgeNotFound(ForgeObject.REVISION), ForgeRejected("Validation Failed")], ids=repr
)
def test_a_degraded_check_read_waits_rather_than_failing(refusal: Exception | None) -> None:
    double = _forge(checks={_SUBMITTED: None}) if refusal is None else _forge(replies={"checks": [refusal]})
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert "delivery-findings" not in sink.markers()


def _failing(name: str = "build", detail: str = "failure", check_id: str = "1") -> Check:
    return check(name, CheckOutcome.FAILED, detail, id=check_id, details_url="https://ci/1")


def test_a_failing_check_answers_failure_and_writes_findings() -> None:
    double = _forge(checks={_SUBMITTED: (_failing(detail="timed_out"),), "main": GREEN})
    answer, sink = _land(double)
    assert answer.choice == "failure"
    findings = sink.markers()["delivery-findings"]
    assert f"## {_WIDGET}#1" in findings
    assert "build: timed_out — https://ci/1" in findings
    assert "base branch: build is clean — this change broke CI" in findings
    assert double.named("merge") == []


def test_a_base_red_check_beside_an_own_failure_still_fails_and_names_both() -> None:
    double = _forge(
        checks={
            _SUBMITTED: (_failing("build"), _failing("lint", check_id="2")),
            "main": (_failing("lint", check_id="9"),),
        }
    )
    answer, sink = _land(double)
    assert answer.choice == "failure"
    findings = sink.markers()["delivery-findings"]
    assert "base branch: build is clean — this change broke CI" in findings
    assert "base branch: also failing lint — not this change" in findings
    assert double.named("rerun_check") == []


def test_a_degraded_base_read_charges_a_head_failure_to_the_chunk() -> None:
    double = _forge(checks={_SUBMITTED: (_failing(),), "main": None})
    answer, _ = _land(double)
    assert answer.choice == "failure"
    assert double.named("rerun_check") == []


def test_an_inherited_only_failure_is_re_run_once_and_waits() -> None:
    double = _forge(checks={_SUBMITTED: (_failing(),), "main": (_failing(),)})
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert double.named("rerun_check") == [(_WIDGET, {"id": "1"})]
    assert sink.markers()[f"ci-rerun/{_WIDGET}/build/{_SUBMITTED}"] == _SUBMITTED


@pytest.mark.parametrize(
    "refusal", [False, ForgeNotFound(ForgeObject.CHECK), ForgeRejected("Resource not accessible")], ids=repr
)
def test_a_refused_re_run_writes_no_rerun_marker_and_routes_failure(refusal: object) -> None:
    double = _forge(checks={_SUBMITTED: (_failing(),), "main": (_failing(),)}, replies={"rerun_check": [refusal]})
    answer, sink = _land(double)
    assert answer.choice == "failure"
    assert not any(name.startswith("ci-rerun/") for name in sink.markers())
    assert "base branch: also failing build — not this change" in sink.markers()["delivery-findings"]


def test_a_re_run_the_forge_did_not_answer_writes_no_marker_and_is_asked_again_next_pass() -> None:
    double = _forge(
        checks={_SUBMITTED: (_failing(),), "main": (_failing(),)}, replies={"rerun_check": [ForgeUnavailable("down")]}
    )
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert not any(name.startswith("ci-rerun/") for name in sink.markers())


def test_a_forge_that_cannot_re_run_checks_skips_the_re_run_and_answers_inherited_failure() -> None:
    double = _forge(checks={_SUBMITTED: (_failing(),), "main": (_failing(),)})
    answer, _ = _land(double, offered=frozenset(Capability) - {Capability.CHECKS_RERUN})
    assert answer.choice == "inherited-failure"
    assert double.named("rerun_check") == []


def test_a_re_run_check_still_red_answers_inherited_failure() -> None:
    double = _forge(checks={_SUBMITTED: (_failing(),), "main": (_failing(),)})
    visit = _visit(markers=(f"ci-rerun/{_WIDGET}/build/{_SUBMITTED}",))
    answer, sink = _land(double, visit)
    assert answer.choice == "inherited-failure"
    findings = sink.markers()["delivery-findings"]
    assert "Inherited from the base — re-run once, still failing (not this change):" in findings
    assert f"signature: ci-rerun/{_WIDGET}/build/{_SUBMITTED}" in findings
    assert "base branch: main" in findings
    assert double.named("rerun_check") == []


def test_an_accepted_re_run_still_red_on_the_next_pass_answers_inherited_failure() -> None:
    double = _forge(checks={_SUBMITTED: (_failing(),), "main": (_failing(),)})
    first, sink = _land(double)
    assert first.choice == "pending"

    answer, _ = _land(double, _visit(markers=tuple(name for name in sink.markers() if name.startswith("ci-rerun/"))))
    assert answer.choice == "inherited-failure"
    assert double.named("rerun_check") == [(_WIDGET, {"id": "1"})]


def test_two_repos_one_failing_names_only_the_failing_one_and_merges_neither() -> None:
    double = _forge(_WIDGET, _GADGET)
    double.repos[_GADGET].checks = {_SUBMITTED: (_failing(),), "main": GREEN}
    answer, sink = _land(double, _visit((_WIDGET, _GADGET)))
    assert answer.choice == "failure"
    findings = sink.markers()["delivery-findings"]
    assert _GADGET in findings and _WIDGET not in findings
    assert double.named("merge") == []


def test_an_empty_commit_set_fails_where_the_graph_expects_commits_and_lands_where_it_does_not() -> None:
    answer, _ = _land(GitHubDouble(), _visit(()))
    assert answer.choice == "failure"
    assert "no git commits to deliver" in answer.log[0]

    answer, _ = _land(GitHubDouble(), _visit((), expects=False))
    assert answer.choice == "landed"


def test_a_fully_marked_commit_set_lands_without_a_forge_call() -> None:
    double = _forge()
    answer, _ = _land(double, _visit(markers=(f"merged/{_WIDGET}",)))
    assert answer.choice == "landed"
    assert double.calls == []


def test_a_branch_adding_no_commit_lands_as_a_no_op_at_the_base_tip() -> None:
    double = _forge(_WIDGET, _GADGET)
    double.repos[_WIDGET].heads = {}
    double.repos[_WIDGET].compares = {("main", "feat/x"): comparison(ComparisonStatus.BEHIND)}

    answer, sink = _land(double, _visit((_WIDGET, _GADGET)))

    assert answer.choice == "landed"
    assert sink.landings()[0] == LandingRecord(repository="re_widget", repo=_WIDGET, revision="base-tip")
    assert [args["number"] for _, args in double.named("merge")] == [1]


def test_a_missing_base_branch_writes_no_landing_and_waits() -> None:
    double = _forge(heads={}, compares={("main", "feat/x"): comparison(ComparisonStatus.IDENTICAL)}, base_tip=None)
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert sink.landings() == []


@pytest.mark.parametrize(
    "refusal", [ForgeNotFound(ForgeObject.REVISION), ForgeRejected("No common ancestor")], ids=repr
)
def test_a_refused_open_whose_base_comparison_is_refused_is_not_nothing_to_land(refusal: Exception) -> None:
    double = _forge(heads={}, replies={"compare": [refusal]})
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert sink.landings() == []


def test_a_refused_open_adopts_the_open_change_someone_else_opened() -> None:
    double = _forge()
    raced = change(_WIDGET, 7, branch="feat/x", sha=_SUBMITTED, base="main")

    def opened_meanwhile(**_: object) -> Change:
        double.repos[_WIDGET].changes[7] = raced
        raise ForgeRejected("A pull request already exists")

    double.repos[_WIDGET].replies["open_change"] = [opened_meanwhile]
    answer, _ = _land(double)
    assert answer.choice == "landed"
    assert [args["number"] for _, args in double.named("merge")] == [7]


def test_a_refused_open_with_nothing_to_adopt_waits() -> None:
    double = _forge(heads={})
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert sink.landings() == []


@pytest.mark.parametrize("refusal", [ForgeNotFound(ForgeObject.CHANGE), ForgeRejected("Server says no")], ids=repr)
def test_a_change_read_the_forge_refused_waits(refusal: Exception) -> None:
    double = _forge(replies={"change": [refusal]})
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert double.named("merge") == [] and sink.landings() == []


def test_a_merge_refused_whose_reread_finds_it_merged_lands_the_merged_revision() -> None:
    double = _forge(replies={"merge": [ForgeRejected("Base branch was modified")]})
    open_change = change(_WIDGET, 1, branch="feat/x", sha=_SUBMITTED, base="main", mergeability=Mergeability.MERGEABLE)
    merged = replace(open_change, state=ChangeState.MERGED, merged_revision="m1")
    double.repos[_WIDGET].replies["change"] = [open_change, merged]
    answer, sink = _land(double)
    assert answer.choice == "landed"
    assert sink.landings()[0].revision == "m1"


def test_a_merge_no_read_names_the_commit_of_writes_no_landing_and_waits() -> None:
    double = _forge(replies={"merge": [ForgeNotFound(ForgeObject.CHANGE)]})
    open_change = change(_WIDGET, 1, branch="feat/x", sha=_SUBMITTED, base="main", mergeability=Mergeability.MERGEABLE)
    double.repos[_WIDGET].replies["change"] = [open_change, replace(open_change, state=ChangeState.MERGED)]
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert sink.landings() == []


def test_a_merge_the_forge_refused_while_the_pr_stays_open_waits() -> None:
    double = _forge(replies={"merge": [ForgeRejected("Pull Request is not mergeable")]})
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert sink.landings() == []


def test_a_pr_merged_by_an_earlier_pass_is_recovered_without_a_second_pr_or_merge() -> None:
    double = _forge(replies={"merge": [ForgeRejected("Pull Request is not mergeable")]})
    double.repos[_WIDGET].changes[4] = change(
        _WIDGET, 4, branch="feat/x", sha=_SUBMITTED, base="main", state=ChangeState.MERGED, merged_revision="m4"
    )
    answer, sink = _land(double)
    assert answer.choice == "landed"
    assert double.named("open_change") == []
    assert sink.landings()[0].revision == "m4"


def test_a_stale_merged_pr_on_a_reused_branch_name_is_not_a_landing() -> None:
    double = _forge()
    double.repos[_WIDGET].changes[4] = change(
        _WIDGET, 4, branch="feat/x", sha="old", base="main", state=ChangeState.MERGED, merged_revision="m4"
    )
    answer, sink = _land(double)
    assert answer.choice == "landed"
    assert [args["number"] for _, args in double.named("merge")] == [2]
    assert sink.landings()[0].revision == f"merge-{_WIDGET}-2"


def test_a_refused_merged_pr_lookup_waits_rather_than_opening_a_duplicate() -> None:
    double = _forge(replies={"changes": [ForgeRejected("Validation Failed")]})
    answer, _ = _land(double)
    assert answer.choice == "pending"
    assert double.named("open_change") == []


def test_a_forge_that_does_not_answer_a_merged_pr_lookup_waits() -> None:
    double = _forge(replies={"changes": [ForgeUnavailable("connection refused")]})
    answer, _ = _land(double)
    assert answer.choice == "pending"
    assert double.named("open_change") == []


# --- the forge answers a person must settle -----------------------------------


@pytest.mark.parametrize("call", ["changes", "open_change", "change", "update_from_base", "merge", "checks"])
def test_a_rejected_credential_on_any_call_but_a_re_run_escalates_forge_auth(call: str) -> None:
    double = _forge(
        mergeability=Mergeability.BEHIND if call == "update_from_base" else Mergeability.MERGEABLE,
        replies={call: [ForgeRejected(ForgeRejected.AUTH)]},
    )
    answer, sink = _land(double)
    assert answer.choice is None and answer.escalation is not None
    assert answer.escalation.cause is EscalationCause.FORGE_AUTH
    assert _WIDGET in answer.escalation.detail and "https://api.github.example" in answer.escalation.detail
    assert sink.landings() == []


def test_a_repository_the_forge_does_not_know_at_branch_tip_escalates_forge_config() -> None:
    double = _forge(replies={"branch_tip": [ForgeNotFound(ForgeObject.REPOSITORY)]})
    double.repos[_WIDGET].changes[4] = change(
        _WIDGET, 4, branch="feat/x", sha="old", base="main", state=ChangeState.MERGED, merged_revision="m4"
    )
    answer, _ = _land(double)
    assert answer.escalation is not None and answer.escalation.cause is EscalationCause.FORGE_CONFIG
    detail = answer.escalation.detail
    assert _WIDGET in detail and "https://api.github.example" in detail
    assert "does not exist, or cannot be seen with this credential" in detail


def test_a_rate_limited_head_gate_compare_waits_until_the_limit_lifts() -> None:
    resume_at = datetime(2026, 10, 24, 12, tzinfo=UTC)
    double = _forge(heads={"feat/x": "merge1"}, replies={"compare": [ForgeRateLimited(resume_at)]})
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert answer.next_poll_at is not None and answer.next_poll_at >= resume_at
    assert "delivery-findings" not in sink.markers()
    assert double.named("merge") == []


# --- the head gate ------------------------------------------------------------


def _commit(sha: str, *parents: str) -> CommitSummary:
    return CommitSummary(sha, parents)


def _file(name: str, blob: str) -> FileChange:
    return FileChange(
        path=name,
        previous_path=None,
        status="modified",
        blob=blob,
        patch="@@ -1 +1 @@\n-a\n+b",
        additions=1,
        deletions=1,
    )


def _advanced(double: GitHubDouble, repo: str, *, merged_files: tuple[FileChange, ...]) -> None:
    """``repo``'s head advanced past the submitted commit by one merge of the base tip ``b1``."""
    state = double.repos[repo]
    state.heads = {"feat/x": "merge1"}
    base_files = (_file("a.txt", "blob1"),)
    state.compares = {
        ("main", "b1"): comparison(ComparisonStatus.IDENTICAL),
        (_SUBMITTED, "b1"): comparison(files=base_files, merge_base="mb"),
        ("mb", _SUBMITTED): comparison(),
        (_SUBMITTED, "merge1"): comparison(commits=(_commit("merge1", _SUBMITTED, "b1"),), files=merged_files),
    }


def test_a_head_advanced_only_by_a_base_merge_lands_the_verified_head() -> None:
    double = _forge()
    _advanced(double, _WIDGET, merged_files=(_file("a.txt", "blob1"),))
    answer, _ = _land(double)
    assert answer.choice == "landed"
    assert double.named("merge")[0][1]["expected_head"] == "merge1"


def test_a_merge_adding_content_beyond_the_base_change_refuses_with_both_findings() -> None:
    double = _forge()
    _advanced(double, _WIDGET, merged_files=(_file("a.txt", "blob1"), _file("evil.txt", "x")))
    answer, sink = _land(double)
    assert answer.choice == "failure"
    markers = sink.markers()
    assert markers["delivery-findings"] == markers["delivery-findings/foreign-head"]
    assert "merge1 (adds content beyond the base branch's change)" in markers["delivery-findings"]
    assert double.writes() == ["open_change"]


def test_a_merge_whose_file_list_the_forge_cut_short_refuses() -> None:
    double = _forge()
    _advanced(double, _WIDGET, merged_files=(_file("a.txt", "blob1"),))
    state = double.repos[_WIDGET]
    state.compares[(_SUBMITTED, "merge1")] = replace(state.compares[(_SUBMITTED, "merge1")], files_truncated=True)
    answer, sink = _land(double)
    assert answer.choice == "failure"
    assert "merge1 (adds content beyond the base branch's change)" in sink.markers()["delivery-findings"]


def test_a_comparison_listing_fewer_commits_than_the_head_is_ahead_refuses_the_head_as_truncated() -> None:
    double = _forge(heads={"feat/x": "merge1"})
    double.repos[_WIDGET].compares = {
        (_SUBMITTED, "merge1"): comparison(commits=(_commit("merge1", _SUBMITTED, "b1"),), commits_truncated=True)
    }
    answer, sink = _land(double)
    assert answer.choice == "failure"
    assert f"the commit list between {_SUBMITTED} and merge1 is truncated" in sink.markers()["delivery-findings"]


def test_a_foreign_non_merge_commit_refuses_and_fires_nothing_in_a_behind_sibling() -> None:
    double = _forge(_WIDGET, _GADGET)
    double.repos[_WIDGET].heads = {"feat/x": "foreign"}
    double.repos[_WIDGET].compares = {(_SUBMITTED, "foreign"): comparison(commits=(_commit("foreign", _SUBMITTED),))}
    double.repos[_GADGET].mergeability = Mergeability.BEHIND

    answer, sink = _land(double, _visit((_WIDGET, _GADGET)))

    assert answer.choice == "failure"
    assert "foreign (not a merge of the base branch)" in sink.markers()["delivery-findings/foreign-head"]
    assert double.named("update_from_base") == [] and double.named("merge") == []


def test_a_merged_pr_whose_head_carries_a_foreign_commit_is_not_a_landing() -> None:
    double = _forge()
    double.repos[_WIDGET].heads = {"feat/x": "foreign"}
    double.repos[_WIDGET].changes[4] = change(
        _WIDGET, 4, branch="feat/x", sha="foreign", base="main", state=ChangeState.MERGED, merged_revision="m4"
    )
    double.repos[_WIDGET].compares = {(_SUBMITTED, "foreign"): comparison(commits=(_commit("foreign", _SUBMITTED),))}
    answer, sink = _land(double)
    assert answer.choice == "failure"
    assert "foreign (not a merge of the base branch)" in sink.markers()["delivery-findings/foreign-head"]
    assert sink.landings() == []


def test_a_merged_pr_whose_head_only_merged_the_base_still_lands() -> None:
    double = _forge(replies={"merge": [ForgeRejected("Pull Request is not mergeable")]})
    _advanced(double, _WIDGET, merged_files=(_file("a.txt", "blob1"),))
    double.repos[_WIDGET].changes[4] = change(
        _WIDGET, 4, branch="feat/x", sha="merge1", base="main", state=ChangeState.MERGED, merged_revision="m4"
    )
    answer, sink = _land(double)
    assert answer.choice == "landed"
    assert sink.landings()[0].revision == "m4"


def test_a_head_that_no_longer_descends_from_the_submitted_commit_refuses() -> None:
    double = _forge(heads={"feat/x": "reset"}, compares={(_SUBMITTED, "reset"): comparison(ComparisonStatus.DIVERGED)})
    answer, sink = _land(double)
    assert answer.choice == "failure"
    assert "does not descend from the submitted commit" in sink.markers()["delivery-findings"]


@pytest.mark.parametrize(
    "refusal", [ForgeNotFound(ForgeObject.REVISION), ForgeRejected("No common ancestor")], ids=repr
)
def test_a_head_gate_compare_the_forge_refused_routes_the_head_as_foreign(refusal: Exception) -> None:
    double = _forge(heads={"feat/x": "merge1"}, replies={"compare": [refusal]})
    answer, sink = _land(double)
    assert answer.choice == "failure"
    assert f"the forge would not compare {_SUBMITTED}...merge1" in sink.markers()["delivery-findings/foreign-head"]


def test_a_head_gate_compare_the_forge_did_not_answer_waits_without_writing() -> None:
    double = _forge(heads={"feat/x": "merge1"}, replies={"compare": [ForgeUnavailable("HTTP 503")]})
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert answer.next_poll_at is None
    assert "delivery-findings" not in sink.markers()
    assert double.named("merge") == []


# --- what the sink answered ---------------------------------------------------


def test_a_fenced_landing_answers_failure_without_touching_the_next_repository() -> None:
    double = _forge(_WIDGET, _GADGET)
    answer, sink = _land(double, _visit((_WIDGET, _GADGET)), _Sink(fence_landings=True))
    assert answer.choice == "failure"
    assert [landing.repo for landing in sink.landings()] == [_WIDGET]
    assert [repo for repo, _ in double.named("merge")] == [_WIDGET]
    assert "no other repository is touched" in answer.log[-1]


def test_an_unrecorded_pr_marker_answers_failure_before_any_merge() -> None:
    double = _forge()
    answer, _ = _land(double, sink=_Sink(refuse=("delivery-pr/",)))
    assert answer.choice == "failure"
    assert double.named("merge") == []


def test_an_unrecorded_wait_finding_still_waits() -> None:
    double = _forge(checks={_SUBMITTED: (check(outcome=CheckOutcome.PENDING, detail="queued"),)})
    answer, _ = _land(double, sink=_Sink(refuse=("delivery-findings",)))
    assert answer.choice == "pending"


# --- through the executor -----------------------------------------------------

_GRAPH = """
name: land-pass
entry: deliver
nodes:
  deliver:
    executor: hub
    action: land
    judgement:
      choices:
        landed: {description: landed., to: done}
        conflict: {description: conflict., to: done}
        inherited-failure: {description: inherited., to: done}
        failure: {description: failure., to: done}
"""

_REPOSITORIES = tuple(
    RepositorySeed(
        forge_api_url=f"http://forge.{host}",
        owner="acme",
        repo=name,
        base_branch="main",
        secret_name="fixture-forge-token",
    )
    for name, host in (("widget", "one"), ("gadget", "one"))
)


def _hub(tmp_path: Path, double: GitHubDouble, repositories: tuple[RepositorySeed, ...] = _REPOSITORIES) -> HubHarness:
    return build_hub(tmp_path, repositories=repositories, forges=double.forges)


def _chunk(hub: HubHarness, *repos: str, forges: tuple[str | None, ...] = ()) -> str:
    minted = hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH})
    assert minted.status_code == 201, minted.text
    item = seed_work_item(
        WorkItemStore(hub_store_connections(hub.engine)),
        graph_id=minted.json()["graph_id"],
        author=WorkItemAuthor.user("u_1"),
        at=hub.clock.now(),
    )
    chunk_id = f"ch_{item.ref}"
    with hub.engine.begin() as conn:
        for index, repo in enumerate(repos):
            conn.execute(
                insert(s.artifacts).values(
                    stamp(
                        s.artifacts,
                        artifact_id=f"ar_{chunk_id}_{index}",
                        chunk_id=chunk_id,
                        node_id="nd_build",
                        node_name="build",
                        epoch=0,
                        name=f"w{index}",
                        kind="git_commit",
                        data=f"feat/x:{_SUBMITTED}",
                        repo=repo,
                        forge=forges[index] if forges else None,
                        produced_at=hub.clock.now(),
                        seq=index + 1,
                    )
                )
            )
    hub.stand_at(chunk_id, "deliver")
    return chunk_id


def _take(hub: HubHarness, chunk_id: str) -> str | None:
    return hub.turn_outcome(chunk_id)


def _landed_rows(hub: HubHarness, chunk_id: str) -> list[tuple[str, str, str | None]]:
    table = s.delivery_repo_landed
    with hub.engine.begin() as conn:
        rows = conn.execute(
            hub_scoped(select(table.c.repo, table.c.commit_hash, table.c.repository_id)).where(
                table.c.chunk_id == chunk_id
            )
        ).all()
    return [tuple(row) for row in rows]


def _artifact(hub: HubHarness, chunk_id: str, name: str) -> str | None:
    return next((a.data for a in hub.services.chunks.artifacts.load_artifacts(chunk_id) if a.name == name), None)


def _record_id(hub: HubHarness, repo: str) -> str:
    rows = hub.services.repository_records.list_all(include_retired=True)
    return next(str(r.repository_id) for r in rows if r.fields.repo == repo)


def test_land_lands_each_repository_through_its_own_handle_and_records_each_landing(tmp_path: Path) -> None:
    double = _forge(_WIDGET, _GADGET)
    hub = _hub(tmp_path, double)
    chunk_id = _chunk(hub, _WIDGET, _GADGET)

    assert _take(hub, chunk_id) == "landed"
    widget, gadget = _record_id(hub, "widget"), _record_id(hub, "gadget")
    assert sorted(_landed_rows(hub, chunk_id)) == sorted(
        [(_WIDGET, f"merge-{_WIDGET}-1", widget), (_GADGET, f"merge-{_GADGET}-1", gadget)]
    )
    assert _artifact(hub, chunk_id, f"merged/{_WIDGET}") == f"merge-{_WIDGET}-1"
    assert set(double.tokens) == {_WIDGET, _GADGET}


def test_a_chunk_stopped_while_land_merges_its_first_repository_keeps_that_landing_and_spares_the_second(
    tmp_path: Path,
) -> None:
    double = _forge(_WIDGET, _GADGET)
    hub = _hub(tmp_path, double)
    chunk_id = _chunk(hub, _WIDGET, _GADGET)

    def stop(_number: int) -> None:
        chunk = hub.services.chunks.record.get(chunk_id)
        assert chunk is not None
        hub.services.stop.stop(chunk, by="operator")

    first = next(iter(double.repos))
    double.repos[first].on_merge = stop

    assert _take(hub, chunk_id) == "failure"
    assert [repo for repo, _ in double.named("merge")] == [first]
    assert _artifact(hub, chunk_id, f"merged/{first}") == f"merge-{first}-1"
    assert _landed_rows(hub, chunk_id) == [(first, f"merge-{first}-1", _record_id(hub, first.partition("/")[2]))]


def test_one_owner_repo_held_for_two_records_fails_before_the_pass(tmp_path: Path) -> None:
    twins = (
        RepositorySeed(
            forge_api_url="http://forge.one",
            owner="acme",
            repo="widget",
            base_branch="main",
            secret_name="fixture-forge-token",
        ),
        RepositorySeed(
            forge_api_url="http://forge.two",
            owner="acme",
            repo="widget",
            base_branch="main",
            secret_name="fixture-forge-token",
        ),
    )
    double = _forge(_WIDGET)
    hub = _hub(tmp_path, double, twins)
    chunk_id = _chunk(
        hub, _WIDGET, _WIDGET, forges=("https://forge.one/acme/widget.git", "https://forge.two/acme/widget.git")
    )

    assert _take(hub, chunk_id) == "failure"  # refused at submit: it never holds a flow
    log = _artifact(hub, chunk_id, "hub-log.repository-ambiguous") or ""
    assert "resolves to two repository records" in log
    assert double.calls == [] and double.tokens == {}


def _event_kinds(hub: HubHarness) -> list[str]:
    logged = [json.loads(e["data"]) for e in emitted_events(hub) if e["event"] == HubEventType.EVENT_LOGGED]
    return [e["kind"] for e in logged]


def test_a_forge_lacking_what_land_requires_escalates_once_before_the_pass(tmp_path: Path) -> None:
    double = _forge(_WIDGET)
    narrower = frozenset(Capability) - {Capability.CHANGES_MERGE_MERGE}
    hub = build_hub(tmp_path, repositories=_REPOSITORIES, forges=double.forges_offering({"github": narrower}))
    chunk_id = _chunk(hub, _WIDGET)

    assert _take(hub, chunk_id) == "escalated"
    assert double.calls == [] and double.tokens == {}
    with hub.engine.begin() as conn:
        rows = conn.execute(hub_scoped(select(s.escalations).where(s.escalations.c.chunk_id == chunk_id))).all()
    assert [(row.epoch, row.cause) for row in rows] == [(2, "forge-capability-missing")]
    assert "changes.merge.merge" in rows[0].detail and "http://forge.one" in rows[0].detail
    log = _artifact(hub, chunk_id, "hub-log.forge-capability-missing") or ""
    assert "does not offer `changes.merge.merge`" in log

    # A second visit finds the chunk escalated and takes nothing.
    assert _take(hub, chunk_id) is None
    # One that raced the first past its read records the escalation as already written, and nothing beside it.
    chunk = hub.services.chunks.record.get(chunk_id)
    graph = hub.services.graphs.get(chunk.graph_id) if chunk is not None else None
    node = graph.node_by_name("deliver") if graph is not None else None
    assert chunk is not None and node is not None
    target = hub.services.repository_records.list_all(include_retired=True)[0]
    resolved = ResolvedRepository(
        forge_id=target.fields.forge_id,
        owner=target.fields.owner,
        base_branch=target.fields.base_branch,
        secret_name=target.fields.secret_name,
        record=str(target.repository_id),
    )
    forge = hub.services.forge_records.get(target.fields.forge_id)
    assert forge is not None
    hub.services.turns._router.escalate_capability_missing(
        chunk, node, "land", resolved, Capability.CHANGES_MERGE_MERGE, forge=forge, epoch=1
    )
    assert _event_kinds(hub).count("forge-capability-missing") == 1
    names = [a.name for a in hub.services.chunks.artifacts.load_artifacts(chunk_id)]
    assert names.count("hub-log.forge-capability-missing") == 1


# --- the landing record's write ------------------------------------------------


def test_a_landing_record_is_written_past_the_fence_once_and_answers_whether_it_would_have_refused(
    tmp_path: Path,
) -> None:
    hub = _hub(tmp_path, GitHubDouble())
    chunk_id = _chunk(hub, _WIDGET)
    artifacts = hub.services.chunks.artifacts

    def record() -> bool:
        return artifacts.record_landing(  # type: ignore[attr-defined]
            chunk_id,
            node_id="nd_deliver",
            node_name="deliver",
            epoch=1,
            admission=EpochAdmission.AT_OR_ABOVE,
            repo=_WIDGET,
            revision="m1",
            repository_id="re_widget",
            at=hub.clock.now(),
        )

    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    hub.services.stop.stop(chunk, by="operator")

    assert record() is True
    assert record() is True
    assert _landed_rows(hub, chunk_id) == [(_WIDGET, "m1", "re_widget")]
    assert [a.data for a in artifacts.load_artifacts(chunk_id) if a.name == f"merged/{_WIDGET}"] == ["m1"]
