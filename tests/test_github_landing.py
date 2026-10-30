"""``GitHubLanding`` against a recorded ``httpx2`` transport: every GitHub state and conclusion mapped to its seam word,
every answer classified into the seam's failures, the 404 repository probe and its memo, the retry schedule, and the
credential's write lock (unit tier)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx2
import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.delivery.actions.landing.merge_on_checks import PullRequest as _PullRequest
from blizzard.hub.delivery.actions.landing.repo import LandingRun as _Run
from blizzard.hub.delivery.actions.landing.repo import RepoLanding as _Target
from blizzard.hub.domain.config.forges import FORGE_KIND_CAPABILITIES, ForgeId
from blizzard.hub.domain.config.repositories import RepositoryId, ResolvedRepository
from blizzard.hub.forge.internal.github_landing import ATTEMPT_TIMEOUT_SECONDS, GitHubLanding
from blizzard.hub.forge.landing import (
    ChangeListing,
    ChangeState,
    CheckOutcome,
    CommitPresence,
    ComparisonStatus,
    ForgeNotFound,
    ForgeObject,
    ForgeRateLimited,
    ForgeRejected,
    ForgeUnavailable,
    Mergeability,
    MergeMethod,
    RepositoryForge,
    UpdateMethod,
    UpdateOutcome,
)
from blizzard.hub.forge.registry import ForgeBinding

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
_REPO = "/repos/acme/widget"

type _Answer = httpx2.Response | Exception


def _reply(status: int, body: Any = None, headers: dict[str, str] | None = None) -> httpx2.Response:
    content = b"" if body is None else json.dumps(body).encode()
    return httpx2.Response(status, content=content, headers=headers)


class _Recorded:
    """Answers each ``(method, path)`` from its queue of recorded answers — the last one repeats — and records every
    request it was sent, with whether the write lock was held as it arrived."""

    def __init__(self, routes: Mapping[tuple[str, str], Sequence[_Answer]], lock: asyncio.Lock | None = None) -> None:
        self._routes = {route: list(answers) for route, answers in routes.items()}
        self._lock = lock
        self.requests: list[httpx2.Request] = []
        self.locked: list[bool] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        self.locked.append(self._lock is not None and self._lock.locked())
        queue = self._routes[(request.method, request.url.path)]
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


class _Harness:
    def __init__(
        self, routes: Mapping[tuple[str, str], Sequence[_Answer]], *, api_url: str = "https://api.github.com"
    ) -> None:
        self.lock = asyncio.Lock()
        self.recorded = _Recorded(routes, self.lock)
        self.clock = FixedClock(_NOW)
        self.sleeps: list[float] = []
        self.lock_during_sleep: list[bool] = []
        self.learned: list[Mapping[str, str]] = []
        binding = ForgeBinding(
            kind="github",
            api_url=api_url,
            owner="acme",
            repo="widget",
            token="tok",
            write_lock=lambda: self.lock,
            learn=self.learned.append,
        )
        self.forge = GitHubLanding(
            binding, clock=self.clock, sleep=self._sleep, transport=httpx2.MockTransport(self.recorded)
        )

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.lock_during_sleep.append(self.lock.locked())

    def run[T](self, call: Callable[[GitHubLanding], Awaitable[T]]) -> T:
        async def go() -> T:
            try:
                return await call(self.forge)
            finally:
                self.forge.close()
                await asyncio.sleep(0)

        return asyncio.run(go())


def _pull(
    *, number: int = 7, state: str = "open", mergeable_state: Any = "clean", merged_at: str | None = None, **extra: Any
) -> dict[str, Any]:
    return {
        "number": number,
        "state": state,
        "mergeable_state": mergeable_state,
        "merged_at": merged_at,
        "head": {"ref": "feat", "sha": "h1"},
        "base": {"ref": "main", "sha": "b1"},
        "html_url": f"https://github.com/acme/widget/pull/{number}",
        "merge_commit_sha": "m1",
        **extra,
    }


def _run(status: Any = "completed", conclusion: Any = "success", **extra: Any) -> dict[str, Any]:
    return {"id": 11, "name": "build", "status": status, "conclusion": conclusion, **extra}


_PRESENT = {("GET", _REPO): [_reply(200, {"full_name": "acme/widget"})]}
_ABSENT = {("GET", _REPO): [_reply(404, {"message": "Not Found"})]}


def test_the_offered_constant_is_the_github_kinds_capabilities() -> None:
    assert FORGE_KIND_CAPABILITIES["github"] == GitHubLanding.OFFERED


# The vocabulary


@pytest.mark.parametrize(
    ("mergeable_state", "mergeability"),
    [
        ("clean", Mergeability.MERGEABLE),
        ("unstable", Mergeability.MERGEABLE),
        ("blocked", Mergeability.BLOCKED),
        ("behind", Mergeability.BEHIND),
        ("dirty", Mergeability.CONFLICTING),
        ("draft", Mergeability.DRAFT),
        ("has_hooks", Mergeability.PENDING),
        ("unknown", Mergeability.PENDING),
        ("a-state-github-adds-later", Mergeability.PENDING),
        (None, Mergeability.PENDING),
    ],
)
def test_a_mergeable_state_maps_to_its_seam_word(mergeable_state: Any, mergeability: Mergeability) -> None:
    harness = _Harness({("GET", f"{_REPO}/pulls/7"): [_reply(200, _pull(mergeable_state=mergeable_state))]})

    change = harness.run(lambda forge: forge.change(7))

    assert change.mergeability is mergeability


def test_a_pull_request_reads_as_a_change() -> None:
    harness = _Harness({("GET", f"{_REPO}/pulls/7"): [_reply(200, _pull())]})

    change = harness.run(lambda forge: forge.change(7))

    assert (change.number, change.head, change.head_revision, change.base, change.base_revision) == (
        7,
        "feat",
        "h1",
        "main",
        "b1",
    )
    assert change.state is ChangeState.OPEN
    assert change.web_url == "https://github.com/acme/widget/pull/7"
    assert change.merged_revision is None


@pytest.mark.parametrize(
    ("merged_at", "state", "merged_revision"),
    [("2026-10-04T11:00:00Z", ChangeState.MERGED, "m1"), (None, ChangeState.CLOSED, None)],
    ids=["merged", "closed"],
)
def test_a_closed_pull_request_is_merged_only_with_merged_at(
    merged_at: str | None, state: ChangeState, merged_revision: str | None
) -> None:
    harness = _Harness({("GET", f"{_REPO}/pulls/7"): [_reply(200, _pull(state="closed", merged_at=merged_at))]})

    change = harness.run(lambda forge: forge.change(7))

    assert (change.state, change.merged_revision) == (state, merged_revision)


@pytest.mark.parametrize(
    ("run", "outcome"),
    [
        (_run("completed", "success"), CheckOutcome.PASSED),
        (_run("completed", "neutral"), CheckOutcome.NEUTRAL),
        (_run("completed", "skipped"), CheckOutcome.NEUTRAL),
        (_run("completed", "failure"), CheckOutcome.FAILED),
        (_run("completed", "timed_out"), CheckOutcome.FAILED),
        (_run("completed", "action_required"), CheckOutcome.FAILED),
        (_run("completed", "cancelled"), CheckOutcome.PENDING),
        (_run("completed", "stale"), CheckOutcome.PENDING),
        (_run("completed", "startup_failure"), CheckOutcome.PENDING),
        (_run("completed", "a-conclusion-github-adds-later"), CheckOutcome.PENDING),
        (_run("completed", None), CheckOutcome.PENDING),
        ({"id": 11, "name": "build", "status": "completed"}, CheckOutcome.PENDING),
        ({"id": 11, "name": "build"}, CheckOutcome.PENDING),
        (_run("queued", None), CheckOutcome.PENDING),
        (_run("in_progress", None), CheckOutcome.PENDING),
        (_run("waiting", None), CheckOutcome.PENDING),
        (_run("requested", None), CheckOutcome.PENDING),
        (_run("completed", 3), CheckOutcome.PENDING),
    ],
    ids=lambda value: (
        str(value) if isinstance(value, CheckOutcome) else f"{value.get('status')}-{value.get('conclusion')}"
    ),
)
def test_a_check_runs_status_and_conclusion_fold_into_one_outcome(run: dict[str, Any], outcome: CheckOutcome) -> None:
    harness = _Harness({("GET", f"{_REPO}/commits/h1/check-runs"): [_reply(200, {"check_runs": [run]})]})

    (check,) = harness.run(lambda forge: forge.checks("h1"))

    assert check.outcome is outcome


def test_a_check_carries_its_forge_word_and_log_link_for_display() -> None:
    run = _run("completed", "failure", id=42, details_url="https://ci/42")
    harness = _Harness({("GET", f"{_REPO}/commits/h1/check-runs"): [_reply(200, {"check_runs": [run]})]})

    (check,) = harness.run(lambda forge: forge.checks("h1"))

    assert (check.id, check.name, check.detail, check.details_url) == ("42", "build", "failure", "https://ci/42")


def test_check_runs_are_paged_past_the_first_hundred() -> None:
    first = [_run(id=n) for n in range(100)]
    harness = _Harness(
        {
            ("GET", f"{_REPO}/commits/h1/check-runs"): [
                _reply(200, {"check_runs": first}),
                _reply(200, {"check_runs": [_run("completed", "failure", id=100)]}),
            ]
        }
    )

    checks = harness.run(lambda forge: forge.checks("h1"))

    assert len(checks) == 101 and checks[-1].outcome is CheckOutcome.FAILED
    assert [r.url.params["page"] for r in harness.recorded.requests] == ["1", "2"]


def test_a_check_run_list_holding_something_other_than_runs_is_unavailable() -> None:
    harness = _Harness({("GET", f"{_REPO}/commits/h1/check-runs"): [_reply(200, {"check_runs": ["build"]})]})

    with pytest.raises(ForgeUnavailable):
        harness.run(lambda forge: forge.checks("h1"))


def test_a_comparison_carries_what_the_head_gate_and_base_merge_read() -> None:
    payload = {
        "status": "ahead",
        "ahead_by": 2,
        "behind_by": 0,
        "total_commits": 2,
        "merge_base_commit": {"sha": "base"},
        "commits": [
            {"sha": "c1", "parents": [{"sha": "base"}]},
            {"sha": "c2", "parents": [{"sha": "c1"}, {"sha": "main"}]},
        ],
        "files": [
            {
                "filename": "b.py",
                "previous_filename": "a.py",
                "status": "renamed",
                "sha": "blob",
                "patch": "@@ -1 +1 @@\n-a\n+b",
                "additions": 1,
                "deletions": 1,
            }
        ],
    }
    harness = _Harness({("GET", f"{_REPO}/compare/base...c2"): [_reply(200, payload)]})

    comparison = harness.run(lambda forge: forge.compare("base", "c2"))

    assert comparison.status is ComparisonStatus.AHEAD
    assert (comparison.ahead, comparison.behind, comparison.merge_base) == (2, 0, "base")
    assert [(c.revision, c.parents) for c in comparison.commits] == [("c1", ("base",)), ("c2", ("c1", "main"))]
    (file,) = comparison.files
    assert (file.path, file.previous_path, file.status, file.blob, file.additions, file.deletions) == (
        "b.py",
        "a.py",
        "renamed",
        "blob",
        1,
        1,
    )
    assert not comparison.commits_truncated and not comparison.files_truncated


def test_a_comparison_shorter_than_its_total_or_at_the_file_cap_says_so() -> None:
    files = [{"filename": f"f{n}", "status": "modified", "additions": 0, "deletions": 0} for n in range(300)]
    payload = {
        "status": "diverged",
        "ahead_by": 300,
        "behind_by": 1,
        "total_commits": 300,
        "commits": [{"sha": "c1", "parents": []}],
        "files": files,
    }
    harness = _Harness({("GET", f"{_REPO}/compare/a...b"): [_reply(200, payload)]})

    comparison = harness.run(lambda forge: forge.compare("a", "b"))

    assert comparison.status is ComparisonStatus.DIVERGED
    assert comparison.commits_truncated and comparison.files_truncated


def test_a_branch_tip_is_its_refs_object_sha() -> None:
    harness = _Harness({("GET", f"{_REPO}/git/ref/heads/main"): [_reply(200, {"object": {"sha": "tip"}})]})

    assert harness.run(lambda forge: forge.branch_tip("main")) == "tip"


def test_a_present_commit_carries_its_authored_instant() -> None:
    body = {"commit": {"author": {"date": "2026-10-01T09:30:00Z"}}}
    harness = _Harness({("GET", f"{_REPO}/commits/c1"): [_reply(200, body)]})

    presence = harness.run(lambda forge: forge.commit_exists("c1"))

    assert presence == CommitPresence(True, datetime(2026, 10, 1, 9, 30, tzinfo=UTC))


def test_changes_filter_by_base_and_head_themselves_and_page_closed_ones() -> None:
    first = [_pull(number=200 - n, state="closed", merged_at="2026-10-01T00:00:00Z") for n in range(99)]
    first.append(_pull(number=1, state="closed") | {"base": {"ref": "release", "sha": "r"}})
    harness = _Harness(
        {
            ("GET", f"{_REPO}/pulls"): [
                _reply(200, first),
                _reply(200, [_pull(number=50, state="closed") | {"head": {"ref": "other", "sha": "o"}}]),
            ]
        }
    )

    changes = harness.run(lambda forge: forge.changes(ChangeListing.CLOSED, "main", "feat"))

    assert [c.number for c in changes] == [200 - n for n in range(99)]
    params = harness.recorded.requests[0].url.params
    assert (params["state"], params["base"], params["head"], params["per_page"]) == (
        "closed",
        "main",
        "acme:feat",
        "100",
    )
    assert len(harness.recorded.requests) == 2


@pytest.mark.component
def test_pending_polls_make_one_conditional_filtered_lookup_each() -> None:
    harness = _Harness({("GET", f"{_REPO}/pulls"): [_reply(200, [], {"ETag": '"v1"'}), _reply(304)]})

    async def poll(forge: GitHubLanding) -> None:
        record = ResolvedRepository(
            forge_id=ForgeId("frg_example"), owner="acme", base_branch="main", secret_name="s", record="re_widget"
        )
        target = _Target(
            run=cast(_Run, None),
            repo="acme/widget",
            branch="feature-branch",
            commit="sha1",
            record=record,
            forge=RepositoryForge(RepositoryId("re_widget"), forge, forge, forge, forge.OFFERED),
            forge_label="example",
        )
        for _ in range(11):
            assert await _PullRequest._merged_for_branch(target) is None

    harness.run(poll)
    requests = harness.recorded.requests
    assert len(requests) == 11
    for request in requests[1:]:
        assert request.url.params["state"] == "closed"
        assert request.url.params["head"] == "acme:feature-branch"
        assert request.headers["if-none-match"] == '"v1"'


def test_open_change_posts_as_the_hub_user() -> None:
    harness = _Harness({("POST", f"{_REPO}/pulls"): [_reply(201, _pull(number=9))]})

    change = harness.run(lambda forge: forge.open_change("feat", "main", "Title", "Body"))

    assert change.number == 9
    sent = json.loads(harness.recorded.requests[0].content)
    assert sent == {"title": "Title", "head": "feat", "base": "main", "body": "Body", "user": "blizzard-hub"}


def test_every_call_carries_the_credential_and_a_thirty_second_timeout() -> None:
    harness = _Harness({("GET", f"{_REPO}/pulls/7"): [_reply(200, _pull())]})

    harness.run(lambda forge: forge.change(7))

    (request,) = harness.recorded.requests
    assert request.headers["authorization"] == "token tok"
    assert request.extensions["timeout"]["read"] == ATTEMPT_TIMEOUT_SECONDS


def test_an_enterprise_server_origin_keeps_its_api_path() -> None:
    harness = _Harness(
        {("GET", f"/api/v3{_REPO}/pulls/7"): [_reply(200, _pull())]}, api_url="https://ghe.example.com/api/v3/"
    )

    harness.run(lambda forge: forge.change(7))

    assert str(harness.recorded.requests[0].url) == "https://ghe.example.com/api/v3/repos/acme/widget/pulls/7"


# Updates, merges, and re-runs


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (_reply(202, {"message": "Updating pull request branch."}), UpdateOutcome.ACCEPTED),
        (_reply(422, {"message": "merge conflict between base and head"}), UpdateOutcome.CONFLICT),
        (_reply(422, {"message": "expected head sha didn't match current head ref"}), UpdateOutcome.REFUSED),
        (_reply(405, {"message": "Pull Request is not mergeable"}), UpdateOutcome.REFUSED),
    ],
    ids=["accepted", "conflict", "other-422", "405"],
)
def test_update_from_base_answers_the_outcome_by_merge(answer: httpx2.Response, outcome: UpdateOutcome) -> None:
    harness = _Harness({("PUT", f"{_REPO}/pulls/7/update-branch"): [answer]})

    update = harness.run(lambda forge: forge.update_from_base(7, "h1"))

    assert (update.outcome, update.method) == (outcome, UpdateMethod.MERGE)
    assert json.loads(harness.recorded.requests[0].content) == {"expected_head_sha": "h1"}


@pytest.mark.parametrize("status", [401, 403])
def test_update_from_base_refused_the_credential_is_auth(status: int) -> None:
    harness = _Harness({("PUT", f"{_REPO}/pulls/7/update-branch"): [_reply(status, {"message": "Forbidden"})]})

    with pytest.raises(ForgeRejected) as raised:
        harness.run(lambda forge: forge.update_from_base(7, "h1"))

    assert raised.value.reason == ForgeRejected.AUTH


@pytest.mark.parametrize("method", list(MergeMethod))
def test_merge_answers_the_landed_revision_under_its_method(method: MergeMethod) -> None:
    harness = _Harness({("PUT", f"{_REPO}/pulls/7/merge"): [_reply(200, {"merged": True, "sha": "landed"})]})

    assert harness.run(lambda forge: forge.merge(7, "h1", method, "msg")) == "landed"
    sent = json.loads(harness.recorded.requests[0].content)
    assert sent == {"commit_message": "msg", "sha": "h1", "merge_method": str(method), "user": "blizzard-hub"}


@pytest.mark.parametrize("status", [405, 409, 422])
def test_merge_refused_is_rejected_with_the_forges_message_and_never_retried(status: int) -> None:
    harness = _Harness(
        {("PUT", f"{_REPO}/pulls/7/merge"): [_reply(status, {"message": "Pull Request is not mergeable"})]}
    )

    with pytest.raises(ForgeRejected) as raised:
        harness.run(lambda forge: forge.merge(7, "h1", MergeMethod.MERGE, "msg"))

    assert raised.value.reason == "Pull Request is not mergeable"
    assert len(harness.recorded.requests) == 1


def test_a_merge_answer_without_its_revision_reads_the_change_for_it() -> None:
    harness = _Harness(
        {
            ("PUT", f"{_REPO}/pulls/7/merge"): [_reply(200, {"merged": True})],
            ("GET", f"{_REPO}/pulls/7"): [_reply(200, _pull(state="closed", merged_at="2026-10-04T11:00:00Z"))],
        }
    )

    assert harness.run(lambda forge: forge.merge(7, "h1", MergeMethod.MERGE, "msg")) == "m1"


@pytest.mark.parametrize(("status", "accepted"), [(201, True), (401, False), (403, False), (404, False), (422, False)])
def test_rerun_check_reruns_the_actions_job_and_answers_whether_the_forge_took_it(status: int, accepted: bool) -> None:
    harness = _Harness({("POST", f"{_REPO}/actions/jobs/11/rerun"): [_reply(status, {"message": "nope"})]})

    assert harness.run(lambda forge: forge.rerun_check("11")) is accepted
    assert [(request.method, request.url.path) for request in harness.recorded.requests] == [
        ("POST", f"{_REPO}/actions/jobs/11/rerun")
    ]


# Failures


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_credential_on_any_other_call_is_auth(status: int) -> None:
    harness = _Harness({("GET", f"{_REPO}/compare/a...b"): [_reply(status, {"message": "Bad credentials"})]})

    with pytest.raises(ForgeRejected) as raised:
        harness.run(lambda forge: forge.compare("a", "b"))

    assert raised.value.reason == ForgeRejected.AUTH


def test_any_other_4xx_is_rejected_with_the_forges_message() -> None:
    harness = _Harness({("POST", f"{_REPO}/pulls"): [_reply(422, {"message": "A pull request already exists"})]})

    with pytest.raises(ForgeRejected) as raised:
        harness.run(lambda forge: forge.open_change("feat", "main", "t", "b"))

    assert raised.value.reason == "A pull request already exists"


@pytest.mark.parametrize(
    ("route", "call", "what"),
    [
        (("GET", f"{_REPO}/pulls/7"), lambda forge: forge.change(7), ForgeObject.CHANGE),
        (
            ("PUT", f"{_REPO}/pulls/7/merge"),
            lambda forge: forge.merge(7, "h", MergeMethod.MERGE, "m"),
            ForgeObject.CHANGE,
        ),
        (("PUT", f"{_REPO}/pulls/7/update-branch"), lambda forge: forge.update_from_base(7, "h"), ForgeObject.CHANGE),
        (("GET", f"{_REPO}/compare/a...b"), lambda forge: forge.compare("a", "b"), ForgeObject.REVISION),
        (("GET", f"{_REPO}/commits/h/check-runs"), lambda forge: forge.checks("h"), ForgeObject.REVISION),
    ],
    ids=["change", "merge", "update-branch", "compare", "checks"],
)
def test_a_404_with_the_repository_present_names_what_the_call_asked_for(
    route: tuple[str, str], call: Callable[[GitHubLanding], Awaitable[object]], what: ForgeObject
) -> None:
    harness = _Harness({route: [_reply(404, {"message": "Not Found"})], **_PRESENT})

    with pytest.raises(ForgeNotFound) as raised:
        harness.run(call)

    assert raised.value.what is what


@pytest.mark.parametrize(
    ("route", "call"),
    [
        (("GET", f"{_REPO}/pulls/7"), lambda forge: forge.change(7)),
        (("GET", f"{_REPO}/git/ref/heads/main"), lambda forge: forge.branch_tip("main")),
        (("GET", f"{_REPO}/commits/c1"), lambda forge: forge.commit_exists("c1")),
    ],
    ids=["change", "branch_tip", "commit_exists"],
)
def test_a_404_with_the_repository_missing_too_is_a_missing_repository(
    route: tuple[str, str], call: Callable[[GitHubLanding], Awaitable[object]]
) -> None:
    harness = _Harness({route: [_reply(404, {"message": "Not Found"})], **_ABSENT})

    with pytest.raises(ForgeNotFound) as raised:
        harness.run(call)

    assert raised.value.what is ForgeObject.REPOSITORY


def test_a_missing_branch_tip_on_a_present_repository_is_none() -> None:
    harness = _Harness({("GET", f"{_REPO}/git/ref/heads/gone"): [_reply(404, {})], **_PRESENT})

    assert harness.run(lambda forge: forge.branch_tip("gone")) is None


def test_a_missing_commit_on_a_present_repository_is_absent() -> None:
    harness = _Harness({("GET", f"{_REPO}/commits/c1"): [_reply(404, {})], **_PRESENT})

    assert harness.run(lambda forge: forge.commit_exists("c1")) == CommitPresence(False)


def test_the_probes_answer_is_kept_for_sixty_seconds() -> None:
    harness = _Harness({("GET", f"{_REPO}/git/ref/heads/gone"): [_reply(404, {})], **_PRESENT})

    async def misses(forge: GitHubLanding) -> None:
        await forge.branch_tip("gone")
        harness.clock.advance(timedelta(seconds=59))
        await forge.branch_tip("gone")
        assert harness.recorded.paths().count(_REPO) == 1
        harness.clock.advance(timedelta(seconds=1))
        await forge.branch_tip("gone")

    harness.run(misses)

    assert harness.recorded.paths().count(_REPO) == 2


@pytest.mark.parametrize(
    ("status", "headers", "body", "resume_after"),
    [
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(_NOW.timestamp()) + 600)}, {}, 600),
        (429, {"retry-after": "30"}, {}, 30),
        (403, {"retry-after": "30", "x-ratelimit-reset": str(int(_NOW.timestamp()) + 600)}, {}, 30),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(_NOW.timestamp()) - 5)}, {}, 60),
        (429, {"retry-after": "0"}, {}, 60),
        (403, {}, {"message": "You have exceeded a secondary rate limit."}, 60),
        (403, {"retry-after": "10"}, {"message": "You have exceeded a secondary rate limit."}, 60),
        (403, {"retry-after": "120"}, {"message": "You have exceeded a secondary rate limit."}, 120),
    ],
    ids=[
        "reset",
        "retry-after",
        "retry-after-wins",
        "past-reset",
        "zero-retry-after",
        "secondary",
        "secondary-floor",
        "secondary-later",
    ],
)
def test_a_rate_limit_says_when_to_resume_and_is_never_retried(
    status: int, headers: dict[str, str], body: dict[str, str], resume_after: int
) -> None:
    harness = _Harness({("GET", f"{_REPO}/pulls/7"): [_reply(status, body, headers)]})

    with pytest.raises(ForgeRateLimited) as raised:
        harness.run(lambda forge: forge.change(7))

    assert raised.value.resume_at == _NOW + timedelta(seconds=resume_after)
    assert len(harness.recorded.requests) == 1 and harness.sleeps == []


def test_a_rate_limited_rerun_is_rate_limited_not_refused() -> None:
    harness = _Harness({("POST", f"{_REPO}/actions/jobs/11/rerun"): [_reply(403, {}, {"x-ratelimit-remaining": "0"})]})

    with pytest.raises(ForgeRateLimited):
        harness.run(lambda forge: forge.rerun_check("11"))


@pytest.mark.parametrize(
    "failure",
    [_reply(502, {}), _reply(500), httpx2.ConnectError("refused"), httpx2.ReadTimeout("timed out")],
    ids=["502", "500", "refused", "timeout"],
)
def test_an_unavailable_forge_is_retried_after_one_then_three_seconds(failure: _Answer) -> None:
    harness = _Harness({("GET", f"{_REPO}/pulls/7"): [failure]})

    with pytest.raises(ForgeUnavailable) as raised:
        harness.run(lambda forge: forge.change(7))

    assert not isinstance(raised.value, ForgeRateLimited)
    assert harness.sleeps == [1.0, 3.0]
    assert len(harness.recorded.requests) == 3


def test_a_retry_that_gets_an_answer_answers_it() -> None:
    harness = _Harness({("GET", f"{_REPO}/pulls/7"): [_reply(503), _reply(200, _pull())]})

    assert harness.run(lambda forge: forge.change(7)).number == 7
    assert harness.sleeps == [1.0]


def test_a_write_holds_the_credentials_lock_around_each_attempt_never_through_the_wait() -> None:
    harness = _Harness({("PUT", f"{_REPO}/pulls/7/merge"): [_reply(502), _reply(200, {"merged": True, "sha": "x"})]})

    harness.run(lambda forge: forge.merge(7, "h1", MergeMethod.MERGE, "msg"))

    assert harness.recorded.locked == [True, True]
    assert harness.lock_during_sleep == [False]


def test_a_read_takes_no_lock() -> None:
    harness = _Harness({("GET", f"{_REPO}/pulls/7"): [_reply(200, _pull())]})

    harness.run(lambda forge: forge.change(7))

    assert harness.recorded.locked == [False]
