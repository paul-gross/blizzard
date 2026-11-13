"""The GitHub work source's 401s and 403s: each path classifies the forge refusing its credential, once the rate gate
has declined the answer as a rate refusal, and carries the refusal with a sanitized diagnostic."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx2
import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.chunk.ports.work_sources import WorkAnnotateError
from blizzard.hub.domain.chunk.status_marker import WorkStatusMarker
from blizzard.hub.domain.kernel.forge_refusals import AuthRefusal, ForgeDiagnostic
from blizzard.hub.forge.budget import ForgeBudgets
from blizzard.hub.forge.rates import rate_dialect
from blizzard.hub.forge.refusals import refusal_reader
from blizzard.hub.work_sources.internal.github_work_source import GitHubWorkSource
from blizzard.hub.work_sources.source import WorkSourceAuthRefused, WorkSourceError

_NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
_FORGE = "frg_example"
_POINTER = WorkRef(source="widget", ref="12")
_TOKEN = "ghp_" + "a1" * 18
_REFUSALS = {
    401: (AuthRefusal.CREDENTIAL_INVALID, "Replace the token"),
    403: (AuthRefusal.PERMISSION_MISSING, "Grant issues=write"),
}


def _refusing(status: int, refused: Callable[[httpx2.Request], bool]) -> Callable[[httpx2.Request], httpx2.Response]:
    """Every request ``refused`` names answers ``status`` with a message leaking a token; the rest succeed."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        if refused(request):
            return httpx2.Response(
                status,
                json={"message": f"Bad credentials {_TOKEN}", "documentation_url": "https://docs.github.com/rest"},
                headers={"X-GitHub-Request-Id": "AB12:34", "X-Accepted-GitHub-Permissions": "issues=write"},
            )
        if request.method == "POST" and request.url.path.endswith("/graphql"):
            return httpx2.Response(200, json={"data": {"repository": {}}})
        if request.method == "GET" and request.url.path.endswith("/comments"):
            return httpx2.Response(200, json=[])
        if request.method == "GET" and request.url.path.endswith("/issues"):
            return httpx2.Response(200, json=[])
        return httpx2.Response(201 if request.method == "POST" else 200, json={"title": "t", "body": "b"})

    return answer


def _source(answer: Callable[[httpx2.Request], httpx2.Response]) -> GitHubWorkSource:
    gate = ForgeBudgets(clock=FixedClock(_NOW), dialects=rate_dialect).sweeping(_FORGE, "gh", "github")
    client = httpx2.Client(base_url="http://forge", transport=httpx2.MockTransport(answer))
    return GitHubWorkSource(
        client,
        source_id="widget",
        slug="widget",
        repo="acme/widget",
        web_base="x",
        gate=gate,
        refusals=refusal_reader("github"),
        forge_id=_FORGE,
    )


def _issue_get(request: httpx2.Request) -> bool:
    return request.method == "GET" and request.url.path == "/repos/acme/widget/issues/12"


def _comments_get(request: httpx2.Request) -> bool:
    return request.method == "GET" and request.url.path.endswith("/comments")


def _graphql(request: httpx2.Request) -> bool:
    return request.url.path.endswith("/graphql")


def _assert_sanitized(diagnostic: ForgeDiagnostic, status: int, operation: str, endpoint: str) -> None:
    assert diagnostic.status == status
    assert diagnostic.operation == operation
    assert diagnostic.endpoint == endpoint
    assert diagnostic.repository == "acme/widget"
    assert diagnostic.request_id == "AB12:34"
    assert _TOKEN not in diagnostic.message
    assert diagnostic.message.startswith("Bad credentials")


_READS = [
    ("fetch", _issue_get, "GET /repos/{owner}/{repo}/issues/{number}"),
    ("fetch", _comments_get, "GET /repos/{owner}/{repo}/issues/{number}/comments"),
    ("fetch_many", _graphql, "POST /graphql"),
]


def _read(source: GitHubWorkSource, operation: str) -> None:
    if operation == "fetch":
        source.fetch(_POINTER)
        return
    outcome = source.fetch_many([_POINTER], include_comments=False)[_POINTER]
    if isinstance(outcome, WorkSourceError):
        raise outcome


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize(("operation", "refused", "endpoint"), _READS)
def test_a_refused_read_raises_its_refusal_with_its_sentence_and_remedy(
    status: int, operation: str, refused: Callable[[httpx2.Request], bool], endpoint: str
) -> None:
    with pytest.raises(WorkSourceAuthRefused) as raised:
        _read(_source(_refusing(status, refused)), operation)

    refusal, remedy = _REFUSALS[status]
    assert raised.value.refusal is refusal
    assert raised.value.retry_after is None
    _assert_sanitized(raised.value.diagnostic, status, operation, endpoint)
    assert remedy in str(raised.value)
    assert _TOKEN not in str(raised.value)


def _labels_post(request: httpx2.Request) -> bool:
    return request.method == "POST" and request.url.path == "/repos/acme/widget/labels"


def _issue_labels_post(request: httpx2.Request) -> bool:
    return request.method == "POST" and request.url.path.endswith("/issues/12/labels")


def _label_delete(request: httpx2.Request) -> bool:
    return request.method == "DELETE"


def _issues_list(request: httpx2.Request) -> bool:
    return request.method == "GET" and request.url.path == "/repos/acme/widget/issues"


def _set(source: GitHubWorkSource) -> object:
    return source.set_status(_POINTER, WorkStatusMarker.INGESTED)


def _clear(source: GitHubWorkSource) -> object:
    return source.clear_status(_POINTER)


def _marked(source: GitHubWorkSource) -> object:
    return source.marked_refs()


_LABELS = "/repos/{owner}/{repo}/issues/{number}/labels"
_ANNOTATIONS = [
    (_set, "set_status", _labels_post, "POST /repos/{owner}/{repo}/labels"),
    (_set, "set_status", _issue_labels_post, f"POST {_LABELS}"),
    (_set, "set_status", _label_delete, f"DELETE {_LABELS}/{{name}}"),
    (_clear, "clear_status", _labels_post, "POST /repos/{owner}/{repo}/labels"),
    (_clear, "clear_status", _label_delete, f"DELETE {_LABELS}/{{name}}"),
    (_marked, "marked_refs", _issues_list, "GET /repos/{owner}/{repo}/issues"),
]


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize(("call", "operation", "refused", "endpoint"), _ANNOTATIONS)
def test_a_refused_annotation_carries_its_refusal_and_forge(
    status: int,
    call: Callable[[GitHubWorkSource], object],
    operation: str,
    refused: Callable[[httpx2.Request], bool],
    endpoint: str,
) -> None:
    with pytest.raises(WorkAnnotateError) as raised:
        call(_source(_refusing(status, refused)))

    refusal = raised.value.refusal
    assert refusal is not None
    assert refusal.forge_id == _FORGE
    assert refusal.classified.refusal is _REFUSALS[status][0]
    _assert_sanitized(refusal.classified.diagnostic, status, operation, endpoint)
    assert _REFUSALS[status][1] in str(raised.value)
    assert _TOKEN not in str(raised.value)


def test_a_rate_refusal_stays_a_rate_refusal_with_no_credential_refusal() -> None:
    reset = _NOW + timedelta(minutes=10)

    def rate(_request: httpx2.Request) -> httpx2.Response:
        headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(reset.timestamp()))}
        return httpx2.Response(403, json={"message": "API rate limit exceeded"}, headers=headers)

    source = _source(rate)
    with pytest.raises(WorkSourceError) as read:
        source.fetch(_POINTER)
    with pytest.raises(WorkAnnotateError) as annotate:
        source.marked_refs()

    assert not isinstance(read.value, WorkSourceAuthRefused)
    assert read.value.retry_after == reset
    assert annotate.value.refusal is None
    assert annotate.value.retry_after == reset


def test_another_failure_carries_no_refusal() -> None:
    source = _source(lambda _request: httpx2.Response(500, json={"message": "boom"}))
    with pytest.raises(WorkSourceError) as read:
        source.fetch(_POINTER)
    with pytest.raises(WorkAnnotateError) as annotate:
        source.marked_refs()

    assert not isinstance(read.value, WorkSourceAuthRefused)
    assert annotate.value.refusal is None
