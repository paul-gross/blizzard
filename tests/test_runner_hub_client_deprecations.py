"""blizzard:compat — the deprecation promise: a runner never calls, sends, or reads what the hub's own
``openapi/hub.openapi.json`` marks deprecated, so a runner at the floor never depends on what the floor
deprecated.

Every runner→hub call is covered: each method of the three ``http_hub`` clients is driven through a
recording transport, and each ``HubProxy`` forward under ``runner/api/`` is read from its source. A new
client method or forward with no driver or no readable path fails the guard, not silently passes."""

from __future__ import annotations

import ast
import inspect
import json
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2
import pytest

from blizzard.runner.hub.client import (
    ClaimRequest,
    HostReport,
    PushedFact,
    TranscriptPush,
)
from blizzard.runner.hub.internal.http_hub import HttpHostHubClient, HttpHubClient, HttpHubRunnerAdmin
from blizzard.runner.node_steps.submissions import Completion, GateSubmission

pytestmark = pytest.mark.component

_ROOT = Path(__file__).resolve().parent.parent
_SPEC: dict[str, Any] = json.loads((_ROOT / "openapi" / "hub.openapi.json").read_text())
_API_DIR = _ROOT / "src" / "blizzard" / "runner" / "api"
_METHODS = ("get", "post", "put", "patch", "delete")


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    body: Any


# --- matching a request to the hub's document ---------------------------------------------------


def _segment_matches(forwarded: str, documented: str) -> bool:
    """A documented ``{param}`` takes any segment; a forwarded ``{}`` hole takes any text, so one that
    stands for a literal segment (``counts/{suffix}``) reaches every operation it could name."""
    if documented.startswith("{") and documented.endswith("}"):
        return True
    hole = re.escape(forwarded).replace(re.escape("{}"), ".+")
    return re.fullmatch(hole, documented) is not None


def _operations(method: str, path: str) -> list[tuple[str, dict[str, Any]]]:
    """The hub operations ``method path`` reaches: a literal path, or a template with ``{}`` holes."""
    segments = path.split("/")
    return [
        (template, methods[method.lower()])
        for template, methods in _SPEC["paths"].items()
        if method.lower() in methods
        and len(template.split("/")) == len(segments)
        and all(_segment_matches(f, d) for f, d in zip(segments, template.split("/"), strict=True))
    ]


def _check_operation(method: str, path: str) -> str | None:
    matched = _operations(method, path)
    if not matched:
        return f"{method} {path} matches no operation in hub.openapi.json"
    for template, operation in matched:
        if operation.get("deprecated"):
            return f"{method} {path} calls {template}, which hub.openapi.json marks deprecated"
    return None


def _resolve(schema: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        schema = _SPEC["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def _deprecated_keys(schema: dict[str, Any] | None, body: Any, trail: str = "") -> list[str]:
    """The body keys, at any depth, whose request-schema property is marked deprecated."""
    if schema is None:
        return []
    schema = _resolve(schema)
    found: list[str] = []
    if isinstance(body, dict):
        properties = schema.get("properties", {})
        for key, value in body.items():
            prop = properties.get(key)
            if prop is None:
                continue
            if _resolve(prop).get("deprecated") or prop.get("deprecated"):
                found.append(f"{trail}{key}")
            found += _deprecated_keys(_member(prop), value, f"{trail}{key}.")
    elif isinstance(body, list) and body:
        items = schema.get("items") or _member(schema)
        for element in body:
            found += _deprecated_keys(items, element, trail)
    return found


def _member(schema: dict[str, Any]) -> dict[str, Any] | None:
    """The schema a property's value is described by: itself, or its non-null union member / array items."""
    for member in (*schema.get("anyOf", []), *schema.get("oneOf", [])):
        if member.get("type") != "null":
            return member.get("items") or member
    return schema.get("items") or schema


def _violations(calls: list[Call]) -> list[str]:
    problems: list[str] = []
    for call in calls:
        problem = _check_operation(call.method, call.path)
        if problem:
            problems.append(problem)
            continue
        for _template, operation in _operations(call.method, call.path):
            media = operation.get("requestBody", {}).get("content", {}).get("application/json", {})
            for key in _deprecated_keys(media.get("schema"), call.body):
                problems.append(f"{call.method} {call.path} sends {key}, which hub.openapi.json marks deprecated")
    return problems


# --- the clients, driven through a recording transport ---------------------------------------------


def _recording() -> tuple[httpx2.Client, list[Call]]:
    calls: list[Call] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content) if request.content else None
        calls.append(Call(request.method, request.url.path, body))
        return httpx2.Response(200, json={})

    return httpx2.Client(base_url="http://hub.test", transport=httpx2.MockTransport(handler)), calls


_TRANSCRIPT = {
    "segment_id": "s",
    "chunk_id": "ch_1",
    "node_id": "n",
    "epoch": 1,
    "spawn_generation": 0,
    "turn_range_start": 0,
    "turn_range_end": 0,
    "final": True,
    "normalizer_version": "1",
    "harness_version": None,
    "turns": [],
}
_FACT = PushedFact(seq=1, kind="chunk.advanced", payload_text="{}")

_HUB_CLIENT: dict[str, Callable[[HttpHubClient], object]] = {
    "peek_queue": lambda c: c.peek_queue([], policy="fifo", project="p"),
    "claim_route": lambda c: c.claim_route(ClaimRequest("ch_1", "ws", ["env"])),
    "submit_completion": lambda c: c.submit_completion("ch_1", Completion(choice="done", epoch=1, from_node_id="n")),
    "submit_decision": lambda c: c.submit_decision("ch_1", GateSubmission(from_node_id="n", epoch=1)),
    "push_facts": lambda c: c.push_facts([_FACT]),
    "push_transcripts": lambda c: c.push_transcripts([TranscriptPush(seq=1, body=_TRANSCRIPT)]),
    "get_envelope": lambda c: c.get_envelope("ch_1"),
    "chunk_statuses": lambda c: c.chunk_statuses(["ch_1"]),
    "hub_advance": lambda c: c.hub_advance("ch_1"),
    "get_question": lambda c: c.get_question("q_1"),
    "register_runner": lambda c: c.register_runner("r", "ws", projects=("p",), subscription="s"),
    "fetch_runner_paused": lambda c: c.fetch_runner_paused("rn_1"),
    "identity": lambda c: c.identity(),
    "rekey_route_token": lambda c: c.rekey_route_token("ch_1"),
    "release": lambda c: c.release("ch_1", epoch=1, route_token=None, reason="throttled"),
}
_ADMIN: dict[str, Callable[[HttpHubRunnerAdmin], object]] = {
    "tenants": lambda a: a.tenants(),
    "add_host": lambda a: a.add_host("h", tenant_id="t", adopt=("rn_1",)),
}
_HOST_CLIENT: dict[str, Callable[[HttpHostHubClient], object]] = {
    "report": lambda h: h.report(HostReport(url="http://h")),
    "record": lambda h: h.record(),
    "push_facts": lambda h: h.push_facts([_FACT]),
    "add_runner": lambda h: h.add_runner("r", projects=("p",)),
    "retire_runner": lambda h: h.retire_runner("rn_1", force=True),
}


def _public_methods(cls: type) -> set[str]:
    return {name for name, _ in inspect.getmembers(cls, inspect.isfunction) if not name.startswith("_")} - {"__init__"}


def _drive(driver: Callable[[Any], object], build: Callable[[httpx2.Client], Any]) -> list[Call]:
    client, calls = _recording()
    with warnings.catch_warnings():
        # A read of a field marked `Field(deprecated=...)` raises here, and is not swallowed below.
        warnings.simplefilter("error", DeprecationWarning)
        try:
            driver(build(client))
        except DeprecationWarning:
            raise
        except Exception:  # the canned reply is not a valid answer; the request was recorded first
            pass
    return calls


def _all_client_calls() -> list[Call]:
    calls: list[Call] = []
    for driver in _HUB_CLIENT.values():
        calls += _drive(driver, HttpHubClient)
    for driver in _ADMIN.values():
        calls += _drive(driver, lambda c: HttpHubRunnerAdmin(c, operator_token="t"))
    for driver in _HOST_CLIENT.values():
        calls += _drive(driver, lambda c: HttpHostHubClient(c, token="t"))
    return calls


@pytest.mark.parametrize(
    ("cls", "drivers"),
    [(HttpHubClient, _HUB_CLIENT), (HttpHubRunnerAdmin, _ADMIN), (HttpHostHubClient, _HOST_CLIENT)],
)
def test_every_client_method_has_a_driver(cls: type, drivers: dict[str, Any]) -> None:
    assert _public_methods(cls) == set(drivers), f"{cls.__name__}: add a driver for each new method"


def test_every_driver_reaches_the_hub() -> None:
    for name, driver in _HUB_CLIENT.items():
        assert _drive(driver, HttpHubClient), f"HttpHubClient.{name} sent no request"
    for name, driver in _ADMIN.items():
        assert _drive(driver, lambda c: HttpHubRunnerAdmin(c, operator_token="t")), name
    for name, driver in _HOST_CLIENT.items():
        assert _drive(driver, lambda c: HttpHostHubClient(c, token="t")), name


def test_no_runner_client_calls_sends_or_reads_what_the_hub_deprecates() -> None:
    assert _violations(_all_client_calls()) == []


# --- the HubProxy forwards under runner/api/ ---------------------------------------------------------------


def _template(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else "{}"
            for part in node.values
        )
    return None


def _is_proxy_of(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "of"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "HubProxy"
    )


def _proxy_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_proxy_of(node.value):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        if isinstance(node, ast.arg) and isinstance(node.annotation, ast.Name) and node.annotation.id == "HubProxy":
            names.add(node.arg)
    return names


def _forwards(source: str, filename: str = "<source>") -> tuple[list[Call], list[str]]:
    """Every ``HubProxy`` forward in ``source`` as a call, and the forwards whose path is not a readable literal."""
    tree = ast.parse(source, filename=filename)
    names = _proxy_names(tree)
    calls: list[Call] = []
    unreadable: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        receiver = node.func.value
        if not (_is_proxy_of(receiver) or (isinstance(receiver, ast.Name) and receiver.id in names)):
            continue
        if node.func.attr in ("get", "post"):
            method, path_node = node.func.attr.upper(), node.args[0] if node.args else None
        elif node.func.attr == "forward":
            method_node = node.args[0] if node.args else None
            literal = method_node.value if isinstance(method_node, ast.Constant) else None
            method = literal.upper() if isinstance(literal, str) else "?"
            path_node = node.args[1] if len(node.args) > 1 else None
        else:
            continue
        path = _template(path_node) if path_node is not None else None
        if path is None:
            unreadable.append(f"{filename}:{node.lineno}")
        else:
            calls.append(Call(method, path, None))
    return calls, unreadable


def _api_forwards() -> tuple[list[Call], list[str]]:
    calls: list[Call] = []
    unreadable: list[str] = []
    for path in sorted(_API_DIR.glob("*.py")):
        if path.name == "hub_proxy.py":
            continue
        found, bad = _forwards(path.read_text(), path.name)
        calls += found
        unreadable += bad
    return calls, unreadable


def test_the_forwards_are_found_and_every_path_is_readable() -> None:
    calls, unreadable = _api_forwards()
    assert calls, "no HubProxy forward found under runner/api/"
    assert unreadable == [], f"a forward path that is not a literal cannot be held to the promise: {unreadable}"


def _delegates(source: str) -> bool:
    """Whether a module hands a ``HubProxy`` it built to another function, whose own forward the scan reads."""
    tree = ast.parse(source)
    names = _proxy_names(tree)
    return any(
        isinstance(node, ast.Call) and any(isinstance(arg, ast.Name) and arg.id in names for arg in node.args)
        for node in ast.walk(tree)
    )


def test_every_hub_proxy_use_is_a_forward_the_scan_reads() -> None:
    """A module that builds a ``HubProxy`` yields a forward the scan reads, or hands the proxy on."""
    for path in sorted(_API_DIR.glob("*.py")):
        source = path.read_text()
        if path.name == "hub_proxy.py" or "HubProxy.of(" not in source:
            continue
        assert _forwards(source, path.name)[0] or _delegates(source), f"{path.name}: the scan reads no forward"


def test_no_hub_proxy_forward_reaches_a_deprecated_operation() -> None:
    calls, _ = _api_forwards()
    assert _violations(calls) == []


# --- the planted case -------------------------------------------------------------------------------------------


_ALIAS = "/api/fleet/chunks/{chunk_id}/pm-items"


def test_the_planted_alias_is_one_the_hub_marks_deprecated() -> None:
    assert _SPEC["paths"][_ALIAS]["get"]["deprecated"] is True


def test_a_client_pointed_at_the_deprecated_alias_fails() -> None:
    client, calls = _recording()
    client.get(_ALIAS.format(chunk_id="ch_1"))
    assert any("pm-items" in p and "deprecated" in p for p in _violations(calls))


def test_a_forward_pointed_at_the_deprecated_alias_fails() -> None:
    source = 'def route(request, chunk_id):\n    return HubProxy.of(request, "w").get(f"/api/fleet/chunks/{chunk_id}/pm-items")\n'
    calls, unreadable = _forwards(source)
    assert unreadable == []
    assert any("pm-items" in p and "deprecated" in p for p in _violations(calls))


def test_a_forward_to_no_operation_fails() -> None:
    calls, _ = _forwards('HubProxy.of(request, "w").get("/api/fleet/no-such-route")')
    assert any("matches no operation" in p for p in _violations(calls))


def test_a_deprecated_request_property_sent_fails() -> None:
    spec = {"type": "object", "properties": {"old": {"type": "string", "deprecated": True}, "new": {"type": "string"}}}
    assert _deprecated_keys(spec, {"new": "a"}) == []
    assert _deprecated_keys(spec, {"old": "a", "new": "b"}) == ["old"]


def test_a_deprecated_property_read_is_an_error_under_the_drive() -> None:
    from pydantic import BaseModel, Field

    class Reply(BaseModel):
        old: str = Field(default="x", deprecated="use new")

    with pytest.raises(DeprecationWarning):
        _drive(lambda _client: Reply(old="y").old, lambda client: client)
