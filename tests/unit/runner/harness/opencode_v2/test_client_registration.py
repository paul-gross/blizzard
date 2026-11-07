"""The OpenCode 2 registration, transport guard, and typed client against an in-memory service."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from blizzard.runner.harness.json_value import JsonValue
from blizzard.runner.harness.opencode_v2.client import (
    Delivery,
    ModelReference,
    OpenCodeServiceClient,
    PermissionDecision,
    PermissionEffect,
    PermissionRule,
    ServiceRequestError,
    SessionCreate,
)
from blizzard.runner.harness.opencode_v2.registration import (
    RegistrationError,
    ServiceRegistration,
    read_registration,
    registration_path,
)
from blizzard.runner.harness.opencode_v2.transport import NonLoopbackServiceError, ServiceRequest, require_loopback
from tests.support.opencode_v2_fakes import REGISTRATION, FakeService, FakeTransport


def _client(service: FakeService | None = None) -> tuple[OpenCodeServiceClient, FakeTransport]:
    transport = FakeTransport(service if service is not None else FakeService())
    return OpenCodeServiceClient(REGISTRATION, transport), transport


def test_registration_path_prefers_xdg_state_home_then_home() -> None:
    assert registration_path({"XDG_STATE_HOME": "/s", "HOME": "/h"}) == Path("/s/opencode/service.json")
    assert registration_path({"XDG_STATE_HOME": "", "HOME": "/h"}) == Path("/h/.local/state/opencode/service.json")
    assert registration_path({}) is None


def test_a_registration_parses_and_never_shows_its_password(tmp_path: Path) -> None:
    path = tmp_path / "service.json"
    path.write_text(
        json.dumps({"id": "abc", "version": "2.1.4", "url": "http://127.0.0.1:49374/", "pid": 7, "password": "pw-x"})
    )
    registration = read_registration(path)
    assert registration == ServiceRegistration("abc", "2.1.4", "http://127.0.0.1:49374", 7, "pw-x")
    assert "pw-x" not in repr(registration)


def test_a_missing_registration_reads_as_no_service(tmp_path: Path) -> None:
    assert read_registration(tmp_path / "service.json") is None


@pytest.mark.parametrize(
    ("raw", "names"),
    [
        ("[]", "not an object"),
        ("{", "not JSON"),
        ('{"id": "a", "version": "2", "url": "u", "pid": true, "password": "p"}', "lacks pid"),
        ('{"version": "2", "url": "u", "pid": 1, "password": ""}', "lacks id, password"),
    ],
)
def test_an_unusable_registration_is_refused_by_name(raw: str, names: str) -> None:
    with pytest.raises(RegistrationError, match=names):
        ServiceRegistration.parse(raw)


@pytest.mark.parametrize("url", ["http://127.0.0.1:1", "http://localhost:1", "http://[::1]:1"])
def test_loopback_addresses_are_admitted(url: str) -> None:
    require_loopback(url)


@pytest.mark.parametrize("url", ["http://10.0.0.1:1", "http://example.com:1", "http://0.0.0.0:1"])
def test_a_non_loopback_address_is_refused(url: str) -> None:
    with pytest.raises(NonLoopbackServiceError):
        require_loopback(url)


def test_every_request_authenticates_with_basic_and_the_registered_password() -> None:
    client, transport = _client()
    client.info()
    header = transport.requests[0].headers["authorization"]
    assert header == "Basic " + base64.b64encode(b"opencode:s3cret-password").decode()
    assert "s3cret-password" not in repr(transport.requests[0])


@pytest.mark.parametrize("path", ["/api/experimental/session/x/wait", "/openapi.json", "/global/health"])
def test_the_client_refuses_an_experimental_or_non_api_path(path: str) -> None:
    client, transport = _client()
    with pytest.raises(ValueError, match="non-experimental"):
        client._request("GET", path)  # pyright: ignore[reportPrivateUsage]  # the guard every operation passes
    assert transport.requests == []


def test_create_sends_the_caller_named_id_location_model_rules_and_metadata() -> None:
    service = FakeService()
    service.routes[("POST", "/api/session")] = lambda request: (200, {"data": json.loads(request.body or b"{}")})
    client, transport = _client(service)
    spec = SessionCreate(
        id="ses_01J0000000000000000000000",
        directory="/work/repo",
        metadata={"blizzard": {"runner": "r1"}},
        rules=(PermissionRule("question", "*", PermissionEffect.DENY),),
        model=ModelReference("openai", "gpt-6.1-sol", "high"),
    )
    created = client.create_session(spec)
    assert (
        json.loads(transport.requests[0].body or b"")
        == created
        == {
            "id": "ses_01J0000000000000000000000",
            "location": {"directory": "/work/repo"},
            "metadata": {"blizzard": {"runner": "r1"}},
            "permissions": [{"action": "question", "resource": "*", "effect": "deny"}],
            "model": {"providerID": "openai", "id": "gpt-6.1-sol", "variant": "high"},
        }
    )


def test_operations_address_their_documented_paths_and_bodies() -> None:
    service = FakeService()
    seen: list[tuple[str, str, object]] = []

    def record(method: str, path: str, answer: JsonValue) -> None:
        def route(request: ServiceRequest) -> tuple[int, JsonValue]:
            seen.append((request.method, path, json.loads(request.body) if request.body else None))
            return 200, answer

        service.routes[(method, path)] = route

    record("PUT", "/api/session/ses_a/environment", None)
    record("POST", "/api/session/ses_a/prompt", {"data": {"id": "msg_1"}})
    record("POST", "/api/session/ses_a/permission/per_1/reply", None)
    record("POST", "/api/session/ses_a/permission/per_2/reply", None)
    record("POST", "/api/session/ses_a/model", None)
    record("PATCH", "/api/session/ses_a", None)
    client, _ = _client(service)
    client.set_environment("ses_a", {"BLIZZARD_LEASE": "l1"})
    client.prompt("ses_a", 'line one\n"quoted"', Delivery.QUEUE)
    client.reply_permission("ses_a", "per_1", PermissionDecision.ONCE)
    client.reply_permission("ses_a", "per_2", PermissionDecision.REJECT, "carry on without it")
    client.switch_model("ses_a", ModelReference("openai", "m"))
    client.update_session("ses_a", rules=())
    assert seen == [
        ("PUT", "/api/session/ses_a/environment", {"variables": {"BLIZZARD_LEASE": "l1"}}),
        ("POST", "/api/session/ses_a/prompt", {"text": 'line one\n"quoted"', "delivery": "queue"}),
        ("POST", "/api/session/ses_a/permission/per_1/reply", {"decision": "once"}),
        ("POST", "/api/session/ses_a/permission/per_2/reply", {"decision": "reject", "message": "carry on without it"}),
        ("POST", "/api/session/ses_a/model", {"model": {"providerID": "openai", "id": "m"}}),
        ("PATCH", "/api/session/ses_a", {"permissions": []}),
    ]


def test_running_sessions_and_children_read_their_listings() -> None:
    service = FakeService(running={"ses_a"}, children={"ses_a": ["ses_b"]})
    client, _ = _client(service)
    assert client.running_sessions() == frozenset({"ses_a"})
    assert [child["id"] for child in client.children("ses_a")] == ["ses_b"]


def test_a_failed_operation_names_itself_and_its_status_without_the_password() -> None:
    service = FakeService()
    service.routes[("GET", "/api/session/ses_x")] = lambda _request: (404, {"error": "missing"})
    client, _ = _client(service)
    with pytest.raises(ServiceRequestError) as raised:
        client.session("ses_x")
    assert (raised.value.operation, raised.value.status) == ("session.get", 404)
    assert "s3cret-password" not in str(raised.value)


def test_the_event_stream_yields_each_data_line_as_an_event() -> None:
    client, transport = _client()
    transport.stream_lines = [
        b"event: message\n",
        b'data: {"type": "session.idle", "data": {"sessionID": "s"}}\n',
        b"\n",
    ]
    with client.events() as events:
        assert list(events) == [{"type": "session.idle", "data": {"sessionID": "s"}}]
