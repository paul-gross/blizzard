"""The host's hub client and the operator's host add — driven against a fake hub via ``httpx.MockTransport``.

Every host call presents the host's own token, whatever the shared client carries, and answers its result, or a typed
refusal when the hub refuses the credential (401, 403) or answers 404; anything else raises ``HubClientError``."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from blizzard import __version__
from blizzard.runner.hub.client import (
    HostAddRefusalReason,
    HostAddRefused,
    HostFactPushAck,
    HostHeldRunner,
    HostRecord,
    HostRefusalReason,
    HostRefused,
    HostReport,
    HostReportedWorkspace,
    HubClientError,
    IssuedHost,
    IssuedIdentity,
    PushedFact,
    RetiredHostRunner,
    SubscriptionDeclaration,
)
from blizzard.runner.hub.internal.http_hub import HttpHostHubClient, HttpHubRunnerAdmin
from blizzard.wire.facts import RunnerFact, RunnerFactBatch

pytestmark = pytest.mark.unit

_Handler = Callable[[httpx.Request], httpx.Response]

_RECORD = {
    "host_id": "hs_1",
    "tenant_id": "tn_1",
    "host_name": "box",
    "registered_at": None,
    "last_seen_at": None,
    "runners": [{"runner_id": "rn_1", "runner_name": "r-claude"}],
}


def _host_client(handler: _Handler) -> HttpHostHubClient:
    # The shared client carries a runner's credential; the host's own token replaces it on every host call.
    client = httpx.Client(
        base_url="http://hub.test", transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer runner"}
    )
    return HttpHostHubClient(client, token="host-tok")


def _answering(method: str, path: str, status: int, body: object, seen: list[httpx.Request]) -> _Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == (method, path)
        assert request.headers["Authorization"] == "Bearer host-tok"
        seen.append(request)
        return httpx.Response(status, json=body)

    return handler


def test_report_sends_the_hosts_facts_and_no_name_and_answers_its_record() -> None:
    seen: list[httpx.Request] = []
    report = HostReport(
        url="https://box.test",
        redirect_uris=("https://box.test/auth/callback",),
        workspaces=(HostReportedWorkspace("ws_1", 3),),
        subscriptions=(SubscriptionDeclaration(slug="anthropic", name="Claude", provider="anthropic"),),
    )
    answer = _host_client(_answering("POST", "/api/fleet/host", 200, _RECORD, seen)).report(report)
    assert answer == HostRecord(
        host_id="hs_1", tenant_id="tn_1", host_name="box", runners=(HostHeldRunner("rn_1", "r-claude"),)
    )
    assert json.loads(seen[0].content) == {
        "url": "https://box.test",
        "redirect_uris": ["https://box.test/auth/callback"],
        "workspaces": [{"workspace_id": "ws_1", "env_capacity": 3}],
        "subscriptions": [{"slug": "anthropic", "name": "Claude", "provider": "anthropic"}],
        "version": __version__,
    }


def test_a_report_the_window_refuses_answers_a_version_refusal_with_the_hubs_detail() -> None:
    refusal = {
        "reason": "too-old",
        "runner_version": "0.1.4",
        "hub_version": "0.3.0",
        "floor": "0.2",
        "detail": "upgrade the host to 0.2 or later",
    }
    answer = _host_client(_answering("POST", "/api/fleet/host", 409, refusal, [])).report(HostReport())
    assert answer == HostRefused(
        reason=HostRefusalReason.VERSION_REFUSED, status=409, detail="upgrade the host to 0.2 or later"
    )


def test_record_reads_the_hosts_own_record() -> None:
    answer = _host_client(_answering("GET", "/api/fleet/host", 200, _RECORD, [])).record()
    assert isinstance(answer, HostRecord) and answer.host_id == "hs_1"


def test_push_facts_posts_the_host_lane_and_answers_its_ack() -> None:
    seen: list[httpx.Request] = []
    ack = {"host_id": "hs_1", "high_water": 2, "applied": [2], "already_applied": [1], "rejected": []}
    answer = _host_client(_answering("POST", "/api/fleet/host/events", 200, ack, seen)).push_facts(
        [PushedFact(seq=1, kind="k", payload_text="{}"), PushedFact(seq=2, kind="k", payload_text='{"a": 1}')]
    )
    assert answer == HostFactPushAck(high_water=2, applied=[2], already_applied=[1], rejected=[])
    assert [f["seq"] for f in json.loads(seen[0].content)["facts"]] == [1, 2]


def test_push_facts_sends_the_stored_payload_text_spliced_into_a_body_equal_to_the_decoded_batch() -> None:
    stored = ['{"slug": "s0", "note": "caf\\u00e9"}', '{"nested": {"a": [1, 2]}, "s": "\\"quoted\\""}', "{}"]
    seen: list[httpx.Request] = []
    ack = {"host_id": "hs_1", "high_water": 3, "applied": [1, 2, 3], "already_applied": [], "rejected": []}
    _host_client(_answering("POST", "/api/fleet/host/events", 200, ack, seen)).push_facts(
        [PushedFact(seq=i, kind="k.v", payload_text=text) for i, text in enumerate(stored, start=1)]
    )
    decoded = RunnerFactBatch(
        facts=[RunnerFact(seq=i, kind="k.v", payload=json.loads(text)) for i, text in enumerate(stored, start=1)]
    )
    assert json.loads(seen[0].content) == decoded.model_dump(mode="json")
    assert RunnerFactBatch.model_validate_json(seen[0].content) == decoded
    assert stored[1].encode() in seen[0].content  # the stored bytes, not a re-dump


def test_add_runner_adds_under_the_host_and_answers_the_minted_identity() -> None:
    seen: list[httpx.Request] = []
    added = {"runner_id": "rn_2", "runner_name": "r2", "token": "tok"}
    answer = _host_client(_answering("POST", "/api/fleet/host/runners", 201, added, seen)).add_runner(
        "r2", projects=("blizzard",)
    )
    assert answer == IssuedIdentity(runner_id="rn_2", runner_name="r2", token="tok")
    assert json.loads(seen[0].content) == {"name": "r2", "projects": ["blizzard"]}


def test_retire_runner_answers_the_retired_runner_and_its_released_chunks() -> None:
    seen: list[httpx.Request] = []
    retired = {
        "runner": {
            "runner_id": "rn_2",
            "runner_name": "r2",
            "connection": "offline",
            "online": False,
            "added_at": "2026-01-01T00:00:00Z",
            "hub_paused": False,
            "retired": True,
        },
        "released_chunk_ids": ["ch_1"],
    }
    answer = _host_client(_answering("POST", "/api/fleet/host/runners/rn_2/retire", 200, retired, seen)).retire_runner(
        "rn_2", force=True
    )
    assert answer == RetiredHostRunner(runner_id="rn_2", released_chunk_ids=("ch_1",))
    assert json.loads(seen[0].content)["force"] is True


_CALLS: list[Callable[[HttpHostHubClient], object]] = [
    lambda c: c.report(HostReport()),
    lambda c: c.record(),
    lambda c: c.push_facts([PushedFact(seq=1, kind="k", payload_text="{}")]),
    lambda c: c.add_runner("r2"),
    lambda c: c.retire_runner("rn_2"),
]


@pytest.mark.parametrize("call", _CALLS)
@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, HostRefusalReason.CREDENTIAL_REFUSED),
        (403, HostRefusalReason.CREDENTIAL_REFUSED),
        (404, HostRefusalReason.NOT_FOUND),
    ],
)
def test_every_host_call_types_a_refused_credential_and_a_not_found(
    call: Callable[[HttpHostHubClient], object], status: int, reason: HostRefusalReason
) -> None:
    answer = call(_host_client(lambda _r: httpx.Response(status, json={"detail": "no"})))
    assert answer == HostRefused(reason=reason, status=status, detail="no")


@pytest.mark.parametrize("call", _CALLS)
@pytest.mark.parametrize("status", [409, 503])
def test_any_other_failure_raises_a_hub_client_error(call: Callable[[HttpHostHubClient], object], status: int) -> None:
    with pytest.raises(HubClientError):
        call(_host_client(lambda _r: httpx.Response(status, text="down")))


@pytest.mark.parametrize("call", _CALLS)
def test_an_unreachable_hub_raises_a_hub_client_error(call: Callable[[HttpHostHubClient], object]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(HubClientError):
        call(_host_client(handler))


def _admin(handler: _Handler, operator_token: str | None = None) -> HttpHubRunnerAdmin:
    client = httpx.Client(base_url="http://hub.test", transport=httpx.MockTransport(handler))
    return HttpHubRunnerAdmin(client, operator_token=operator_token)


@pytest.mark.parametrize(("operator_token", "authorization"), [("op-session", "Bearer op-session"), (None, None)])
def test_add_host_posts_the_name_under_the_operators_session_when_one_is_held(
    operator_token: str | None, authorization: str | None
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("POST", "/api/hosts")
        assert json.loads(request.content) == {"name": "box"}
        assert request.headers.get("Authorization") == authorization
        assert request.headers.get("X-Blizzard-Tenant") == "tn_1"
        return httpx.Response(201, json={"host_id": "hs_1", "host_name": "box", "token": "tok", "adopted": []})

    issued = _admin(handler, operator_token).add_host("box", tenant_id="tn_1")
    assert issued == IssuedHost(host_id="hs_1", host_name="box", token="tok")


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (401, {"detail": "no"}, HostAddRefusalReason.SIGN_IN_REQUIRED),
        (403, {"detail": "no"}, HostAddRefusalReason.FORBIDDEN),
        (404, {"detail": "Not Found"}, HostAddRefusalReason.UNSUPPORTED),
        (405, {"detail": "no"}, HostAddRefusalReason.UNSUPPORTED),
        (404, {"detail": "no such tenant"}, HostAddRefusalReason.NO_SUCH_TENANT),
        (409, {"detail": "host name box is taken"}, HostAddRefusalReason.NAME_TAKEN),
        (409, {"detail": {"code": "tenant_required"}}, HostAddRefusalReason.TENANT_REQUIRED),
    ],
)
def test_add_host_refusals_carry_their_reason(status: int, body: object, reason: HostAddRefusalReason) -> None:
    with pytest.raises(HostAddRefused) as refused:
        _admin(lambda _r: httpx.Response(status, json=body)).add_host("box")
    assert refused.value.reason is reason


def test_add_host_server_error_is_a_plain_hub_client_error() -> None:
    with pytest.raises(HubClientError) as raised:
        _admin(lambda _r: httpx.Response(503, text="down")).add_host("box")
    assert type(raised.value) is HubClientError
