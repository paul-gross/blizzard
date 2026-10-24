"""``land`` against the in-memory GitHub double: each outcome its stages answer, the markers they record,
and — through the executor — the landing record a merge writes past the fence (component tier)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

import httpx2
import pytest
from sqlalchemy import insert, select

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.hub.delivery.actions.land import Land
from blizzard.hub.delivery.forge_handle import ForgeReply, ForgeUnreachable
from blizzard.hub.delivery.internal.github_handle import GitHubHandle
from blizzard.hub.domain.actions.effects import Effect, EffectResult, EffectStatus, LandingRecord, Marker, Pass
from blizzard.hub.domain.actions.visit import GitCommits, LandingProject, Visit, VisitChunk, VisitNode
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.config.repositories import (
    RepositoryFields,
    RepositoryOutcome,
    RepositoryResolution,
    ResolvedRepository,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.github_double import GREEN, DoubleRepo, GitHubDouble, pull_body, unreachable
from tests.support import HubHarness, build_hub, hub_scoped, hub_store_connections, seed_work_item, stamp

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
            forge_api_url="https://api.github.example",
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
        ),
        work_items=tuple(MappingProxyType(item) for item in work_items),
        feature_title=feature_title,
        public_url=public_url,
        project=LandingProject(),
    )


def _forge(*repos: str, **state: Any) -> GitHubDouble:
    double = GitHubDouble()
    for repo in repos or (_WIDGET,):
        double.repos[repo] = DoubleRepo(**{"heads": {"feat/x": _SUBMITTED}, **state})
    return double


def _land(double: GitHubDouble, visit: Visit | None = None, sink: _Sink | None = None) -> tuple[Pass, _Sink]:
    visit = visit or _visit()
    sink = sink or _Sink()
    forges = MappingProxyType({t.record: double.handle(t, "tok") for t in visit.resolution.targets})
    return asyncio.run(Land().take(visit, forges, sink)), sink


def test_a_clean_green_pr_merges_its_current_head_and_hands_over_the_landing() -> None:
    double = _forge()

    answer, sink = _land(double)

    assert answer.choice == "landed"
    ((_, merge),) = double.named("merge")
    assert (merge["sha"], merge["method"]) == (_SUBMITTED, "merge")
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

    ((_, opened),) = double.named("open_pull")
    assert opened["title"] == "Add the widget"
    assert opened["body"] == (
        "Delivered by blizzard.\n\nWork items:\n- Refs acme/widget#7\n- default:8\n\nChunk: ch_1\n\n"
        "Board: https://board.example/board/chunk/ch_1\n"
    )
    assert double.named("merge")[0][1]["commit_message"] == "Add the widget\n\nRefs acme/widget#7\ndefault:8"


def test_without_a_feature_title_the_branch_and_the_land_string_stand_in_and_a_long_title_is_cut() -> None:
    double = _forge()
    _land(double)
    assert double.named("open_pull")[0][1]["title"] == "feat/x"
    assert double.named("merge")[0][1]["commit_message"] == f"blizzard: land {_WIDGET}"

    long = _forge()
    _land(long, _visit(feature_title="x" * 300))
    title = long.named("open_pull")[0][1]["title"]
    assert len(title) == 256 and title.endswith("…")


def test_a_dirty_pr_answers_conflict_without_merging() -> None:
    double = _forge(mergeable_state="dirty")
    answer, sink = _land(double)
    assert answer.choice == "conflict"
    assert double.named("merge") == [] and sink.landings() == []


def test_a_behind_pr_fires_update_branch_on_its_head_and_waits() -> None:
    double = _forge(mergeable_state="behind")
    answer, _ = _land(double)
    assert answer.choice == "pending"
    assert double.named("update_branch")[0][1]["expected_head_sha"] == _SUBMITTED
    assert double.named("merge") == []


def test_an_update_branch_conflict_answers_conflict() -> None:
    double = _forge(mergeable_state="behind", update_reply=ForgeReply(422, {"message": "merge conflict"}))
    answer, _ = _land(double)
    assert answer.choice == "conflict"


@pytest.mark.parametrize("state", ["blocked", "clean", "unstable"])
def test_a_watched_head_with_checks_still_running_waits_and_names_them(state: str) -> None:
    double = _forge(mergeable_state=state, checks={_SUBMITTED: [{"name": "build", "status": "in_progress"}]})
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert double.named("merge") == []
    assert "Still running:\n  - build: in_progress" in sink.markers()["delivery-findings"]


def test_a_blocked_head_with_green_checks_merges() -> None:
    double = _forge(mergeable_state="blocked")
    answer, _ = _land(double)
    assert answer.choice == "landed"


def test_an_unknown_state_never_reads_check_runs_nor_writes_findings() -> None:
    double = _forge(mergeable_state="unknown")
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert double.named("check_runs") == []
    assert "delivery-findings" not in sink.markers()


def test_an_empty_check_runs_list_waits_without_findings() -> None:
    double = _forge(checks={_SUBMITTED: []})
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert "delivery-findings" not in sink.markers()


def test_a_degraded_check_runs_read_waits_rather_than_failing() -> None:
    double = _forge(checks={_SUBMITTED: None})
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert "delivery-findings" not in sink.markers()


def _failing(name: str = "build", conclusion: str = "failure", check_id: int = 1) -> dict[str, Any]:
    return {
        "id": check_id,
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
        "details_url": "https://ci/1",
    }


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "action_required"])
def test_a_terminal_check_failure_answers_failure_and_writes_findings(conclusion: str) -> None:
    double = _forge(checks={_SUBMITTED: [_failing(conclusion=conclusion)], "main": GREEN})
    answer, sink = _land(double)
    assert answer.choice == "failure"
    findings = sink.markers()["delivery-findings"]
    assert f"## {_WIDGET}#1" in findings
    assert f"build: {conclusion} — https://ci/1" in findings
    assert "base branch: build is clean — this change broke CI" in findings
    assert double.named("merge") == []


def test_a_cancelled_check_is_re_polled_not_failed() -> None:
    double = _forge(checks={_SUBMITTED: [_failing(conclusion="cancelled")]})
    answer, _ = _land(double)
    assert answer.choice == "pending"


def test_a_base_red_check_beside_an_own_failure_still_fails_and_names_both() -> None:
    double = _forge(
        checks={
            _SUBMITTED: [_failing("build"), _failing("lint", check_id=2)],
            "main": [_failing("lint", check_id=9)],
        }
    )
    answer, sink = _land(double)
    assert answer.choice == "failure"
    findings = sink.markers()["delivery-findings"]
    assert "base branch: build is clean — this change broke CI" in findings
    assert "base branch: also failing lint — not this change" in findings
    assert double.named("rerequest") == []


def test_an_inherited_only_failure_is_re_requested_once_and_waits() -> None:
    double = _forge(checks={_SUBMITTED: [_failing()], "main": [_failing()]})
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert double.named("rerequest") == [(_WIDGET, {"check_id": 1})]
    assert sink.markers()[f"ci-rerun/{_WIDGET}/build/{_SUBMITTED}"] == _SUBMITTED


def test_a_refused_rerequest_writes_no_rerun_marker() -> None:
    double = _forge(
        checks={_SUBMITTED: [_failing()], "main": [_failing()]}, replies={"rerequest": [ForgeReply(403, {})]}
    )
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert not any(name.startswith("ci-rerun/") for name in sink.markers())


def test_a_re_requested_check_still_red_answers_inherited_failure() -> None:
    double = _forge(checks={_SUBMITTED: [_failing()], "main": [_failing()]})
    visit = _visit(markers=(f"ci-rerun/{_WIDGET}/build/{_SUBMITTED}",))
    answer, sink = _land(double, visit)
    assert answer.choice == "inherited-failure"
    findings = sink.markers()["delivery-findings"]
    assert "Inherited from the base — re-run once, still failing (not this change):" in findings
    assert f"signature: ci-rerun/{_WIDGET}/build/{_SUBMITTED}" in findings
    assert "base branch: main" in findings
    assert double.named("rerequest") == []


def test_two_repos_one_failing_names_only_the_failing_one_and_merges_neither() -> None:
    double = _forge(_WIDGET, _GADGET)
    double.repos[_GADGET].checks = {_SUBMITTED: [_failing()], "main": GREEN}
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
    double.repos[_WIDGET].compares = {("main", "feat/x"): {"status": "behind"}}

    answer, sink = _land(double, _visit((_WIDGET, _GADGET)))

    assert answer.choice == "landed"
    assert sink.landings()[0] == LandingRecord(repository="re_widget", repo=_WIDGET, revision="base-tip")
    assert [args["number"] for _, args in double.named("merge")] == [1]


def test_an_unreadable_no_op_base_tip_writes_no_landing_and_waits() -> None:
    double = _forge(heads={}, compares={("main", "feat/x"): {"status": "identical"}}, base_tip="")
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert sink.landings() == []


def test_a_refused_open_that_is_not_an_empty_branch_waits() -> None:
    double = _forge(heads={})
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert sink.landings() == []


def test_a_merge_answer_without_a_sha_lands_the_reread_merge_commit() -> None:
    double = _forge(replies={"merge": [ForgeReply(200, {"merged": True})]})
    double.repos[_WIDGET].on_merge = None

    def merged_pull(number: int) -> ForgeReply:
        return ForgeReply(
            200,
            pull_body(
                _WIDGET, number, branch="feat/x", sha=_SUBMITTED, base="main", merged=True, merge_commit_sha="m1"
            ),
        )

    double.repos[_WIDGET].replies["pull"] = [
        ForgeReply(200, pull_body(_WIDGET, 1, branch="feat/x", sha=_SUBMITTED, base="main", mergeable_state="clean")),
        merged_pull,
    ]
    answer, sink = _land(double)
    assert answer.choice == "landed"
    assert sink.landings()[0].revision == "m1"


def test_a_merge_no_read_names_the_commit_of_writes_no_landing_and_waits() -> None:
    double = _forge(replies={"merge": [ForgeReply(200, {"merged": True})]})
    double.repos[_WIDGET].replies["pull"] = [
        ForgeReply(200, pull_body(_WIDGET, 1, branch="feat/x", sha=_SUBMITTED, base="main", mergeable_state="clean")),
        ForgeReply(200, pull_body(_WIDGET, 1, branch="feat/x", sha=_SUBMITTED, base="main", merged=True)),
    ]
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert sink.landings() == []


def test_a_merge_the_forge_refused_while_the_pr_stays_open_waits() -> None:
    double = _forge(replies={"merge": [ForgeReply(405, {"message": "not mergeable"})]})
    answer, sink = _land(double)
    assert answer.choice == "pending"
    assert sink.landings() == []


def test_a_pr_merged_by_an_earlier_pass_is_recovered_without_a_second_pr_or_merge() -> None:
    double = _forge(replies={"merge": [ForgeReply(405, {"message": "Pull Request is not mergeable"})]})
    double.repos[_WIDGET].pulls[4] = pull_body(
        _WIDGET, 4, branch="feat/x", sha=_SUBMITTED, base="main", merged=True, merged_at="t", merge_commit_sha="m4"
    )
    answer, sink = _land(double)
    assert answer.choice == "landed"
    assert double.named("open_pull") == []
    assert sink.landings()[0].revision == "m4"


def test_a_stale_merged_pr_on_a_reused_branch_name_is_not_a_landing() -> None:
    double = _forge()
    double.repos[_WIDGET].pulls[4] = pull_body(
        _WIDGET, 4, branch="feat/x", sha="old", base="main", merged=True, merged_at="t", merge_commit_sha="m4"
    )
    answer, sink = _land(double)
    assert answer.choice == "landed"
    assert [args["number"] for _, args in double.named("merge")] == [2]
    assert sink.landings()[0].revision == f"merge-{_WIDGET}-2"


def test_a_degraded_merged_pr_lookup_waits_rather_than_opening_a_duplicate() -> None:
    double = _forge(replies={"closed_pulls": [ForgeReply(502, {})]})
    answer, _ = _land(double)
    assert answer.choice == "pending"
    assert double.named("open_pull") == []


def test_an_unreachable_forge_on_a_merged_pr_lookup_escapes_the_pass() -> None:
    double = _forge(replies={"closed_pulls": [unreachable()]})
    with pytest.raises(ForgeUnreachable):
        _land(double)


# --- the head gate ------------------------------------------------------------


def _commit(sha: str, *parents: str) -> dict[str, Any]:
    return {"sha": sha, "parents": [{"sha": p} for p in parents]}


def _file(name: str, blob: str) -> dict[str, Any]:
    return {"filename": name, "sha": blob, "patch": "@@ -1 +1 @@\n-a\n+b", "additions": 1, "deletions": 1}


def _advanced(double: GitHubDouble, repo: str, *, merged_files: list[dict[str, Any]]) -> None:
    """``repo``'s head advanced past the submitted commit by one merge of the base tip ``b1``."""
    state = double.repos[repo]
    state.heads = {"feat/x": "merge1"}
    base_files = [_file("a.txt", "blob1")]
    state.compares = {
        ("main", "b1"): {"status": "identical"},
        (_SUBMITTED, "b1"): {"files": base_files, "merge_base_commit": {"sha": "mb"}},
        ("mb", _SUBMITTED): {"files": []},
        (_SUBMITTED, "merge1"): {
            "status": "ahead",
            "total_commits": 1,
            "commits": [_commit("merge1", _SUBMITTED, "b1")],
            "files": merged_files,
        },
    }


def test_a_head_advanced_only_by_a_base_merge_lands_the_verified_head() -> None:
    double = _forge()
    _advanced(double, _WIDGET, merged_files=[_file("a.txt", "blob1")])
    answer, _ = _land(double)
    assert answer.choice == "landed"
    assert double.named("merge")[0][1]["sha"] == "merge1"


def test_a_merge_adding_content_beyond_the_base_change_refuses_with_both_findings() -> None:
    double = _forge()
    _advanced(double, _WIDGET, merged_files=[_file("a.txt", "blob1"), _file("evil.txt", "x")])
    answer, sink = _land(double)
    assert answer.choice == "failure"
    markers = sink.markers()
    assert markers["delivery-findings"] == markers["delivery-findings/foreign-head"]
    assert "merge1 (adds content beyond the base branch's change)" in markers["delivery-findings"]
    assert double.writes() == ["open_pull"]


def test_a_foreign_non_merge_commit_refuses_and_fires_nothing_in_a_behind_sibling() -> None:
    double = _forge(_WIDGET, _GADGET)
    double.repos[_WIDGET].heads = {"feat/x": "foreign"}
    double.repos[_WIDGET].compares = {
        (_SUBMITTED, "foreign"): {"status": "ahead", "total_commits": 1, "commits": [_commit("foreign", _SUBMITTED)]}
    }
    double.repos[_GADGET].mergeable_state = "behind"

    answer, sink = _land(double, _visit((_WIDGET, _GADGET)))

    assert answer.choice == "failure"
    assert "foreign (not a merge of the base branch)" in sink.markers()["delivery-findings/foreign-head"]
    assert double.named("update_branch") == [] and double.named("merge") == []


def test_a_merged_pr_whose_head_carries_a_foreign_commit_is_not_a_landing() -> None:
    double = _forge()
    double.repos[_WIDGET].heads = {"feat/x": "foreign"}
    double.repos[_WIDGET].pulls[4] = pull_body(
        _WIDGET, 4, branch="feat/x", sha="foreign", base="main", merged=True, merged_at="t", merge_commit_sha="m4"
    )
    double.repos[_WIDGET].compares = {
        (_SUBMITTED, "foreign"): {"status": "ahead", "total_commits": 1, "commits": [_commit("foreign", _SUBMITTED)]}
    }
    answer, sink = _land(double)
    assert answer.choice == "failure"
    assert "foreign (not a merge of the base branch)" in sink.markers()["delivery-findings/foreign-head"]
    assert sink.landings() == []


def test_a_merged_pr_whose_head_only_merged_the_base_still_lands() -> None:
    double = _forge()
    _advanced(double, _WIDGET, merged_files=[_file("a.txt", "blob1")])
    double.repos[_WIDGET].pulls[4] = pull_body(
        _WIDGET, 4, branch="feat/x", sha="merge1", base="main", merged=True, merged_at="t", merge_commit_sha="m4"
    )
    answer, sink = _land(double)
    assert answer.choice == "landed"
    assert sink.landings()[0].revision == f"merge-{_WIDGET}-4"


def test_a_head_that_no_longer_descends_from_the_submitted_commit_refuses() -> None:
    double = _forge(heads={"feat/x": "reset"}, compares={(_SUBMITTED, "reset"): {"status": "diverged"}})
    answer, sink = _land(double)
    assert answer.choice == "failure"
    assert "does not descend from the submitted commit" in sink.markers()["delivery-findings"]


def test_a_degraded_compare_read_waits_without_writing() -> None:
    double = _forge(heads={"feat/x": "merge1"}, replies={"compare": [ForgeReply(503, {})]})
    answer, sink = _land(double)
    assert answer.choice == "pending"
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
    double = _forge(checks={_SUBMITTED: [{"name": "build", "status": "queued"}]})
    answer, _ = _land(double, sink=_Sink(refuse=("delivery-findings",)))
    assert answer.choice == "pending"


# --- the GitHub handle --------------------------------------------------------


def test_the_github_handle_carries_its_credential_and_a_timeout_on_every_call() -> None:
    seen: list[httpx2.Request] = []

    def transport(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"merged": True, "sha": "m"})

    handle = GitHubHandle("https://api.github.example/", "tok", timeout=7.0)

    async def merge() -> ForgeReply:
        await handle.open()
        handle._client._transport = httpx2.MockTransport(transport)  # type: ignore[union-attr]
        try:
            return await handle.merge(_WIDGET, 3, sha="h", commit_message="m", method="merge")
        finally:
            await handle.close()

    reply = asyncio.run(merge())

    assert reply == ForgeReply(200, {"merged": True, "sha": "m"})
    (request,) = seen
    assert str(request.url) == f"https://api.github.example/repos/{_WIDGET}/pulls/3/merge"
    assert request.headers["Authorization"] == "token tok"
    assert json.loads(request.content)["merge_method"] == "merge"
    assert request.extensions["timeout"]["read"] == 7.0


def test_a_github_handle_call_that_gets_no_answer_is_unreachable() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    handle = GitHubHandle("https://api.github.example", "tok")

    async def read() -> ForgeReply:
        await handle.open()
        handle._client._transport = httpx2.MockTransport(refuse)  # type: ignore[union-attr]
        try:
            return await handle.pull(_WIDGET, 1)
        finally:
            await handle.close()

    with pytest.raises(ForgeUnreachable):
        asyncio.run(read())


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
    RepositoryFields(
        forge_api_url=f"http://forge.{host}",
        owner="acme",
        repo=name,
        base_branch="main",
        secret_name="fixture-forge-token",
    )
    for name, host in (("widget", "one"), ("gadget", "one"))
)


def _hub(
    tmp_path: Path, double: GitHubDouble, repositories: tuple[RepositoryFields, ...] = _REPOSITORIES
) -> HubHarness:
    return build_hub(tmp_path, repositories=repositories, forge_handles=double.handle)


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
    return chunk_id


def _take(hub: HubHarness, chunk_id: str):  # type: ignore[no-untyped-def]
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None
    node = graph.node_by_name("deliver")
    assert node is not None
    return hub.services.hub_node.run(chunk, graph, node, epoch=1)


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
    rows = hub.services.hub_node._repositories.list_all(include_retired=True)
    return next(str(r.repository_id) for r in rows if r.fields.repo == repo)


def test_land_lands_each_repository_through_its_own_handle_and_records_each_landing(tmp_path: Path) -> None:
    double = _forge(_WIDGET, _GADGET)
    hub = _hub(tmp_path, double)
    chunk_id = _chunk(hub, _WIDGET, _GADGET)

    result = _take(hub, chunk_id)

    assert result is not None and result.outcome_choice == "landed"
    widget, gadget = _record_id(hub, "widget"), _record_id(hub, "gadget")
    assert sorted(_landed_rows(hub, chunk_id)) == sorted(
        [(_WIDGET, f"merge-{_WIDGET}-1", widget), (_GADGET, f"merge-{_GADGET}-1", gadget)]
    )
    assert _artifact(hub, chunk_id, f"merged/{_WIDGET}") == f"merge-{_WIDGET}-1"
    assert set(double.tokens) == {widget, gadget}
    assert double.opened == double.closed == 2


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

    result = _take(hub, chunk_id)

    assert result is not None and result.outcome_choice == "failure"
    assert [repo for repo, _ in double.named("merge")] == [first]
    assert _artifact(hub, chunk_id, f"merged/{first}") == f"merge-{first}-1"
    assert _landed_rows(hub, chunk_id) == [(first, f"merge-{first}-1", _record_id(hub, first.partition("/")[2]))]


def test_one_owner_repo_held_for_two_records_fails_before_the_pass(tmp_path: Path) -> None:
    twins = (
        RepositoryFields(
            forge_api_url="http://forge.one",
            owner="acme",
            repo="widget",
            base_branch="main",
            secret_name="fixture-forge-token",
        ),
        RepositoryFields(
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

    result = _take(hub, chunk_id)

    assert result is not None and result.outcome_choice == "failure"
    log = _artifact(hub, chunk_id, "hub-log.repository-ambiguous") or ""
    assert "resolves to two repository records" in log
    assert double.calls == [] and double.opened == 0


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
