"""Every session, message, part, event, child, permission, form, compaction, error, usage, and model shape the committed
OpenCode 2 corpus captured parses through the binding's production readers, and each reader refuses a record missing a
field it reads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.json_value import JsonObject, JsonValue
from blizzard.runner.harness.opencode_v2.client import ModelReference
from blizzard.runner.harness.opencode_v2.errors import ErrorKind, classify_error
from blizzard.runner.harness.opencode_v2.shapes import (
    OpenCodeTwoShapeError,
    parse_error,
    parse_event,
    parse_form_request,
    parse_listed_model,
    parse_message,
    parse_permission_request,
    parse_session,
)
from blizzard.runner.harness.opencode_v2.version import ADMITTED_OPENCODE_V2_RANGE

_CORPUS = CommittedCorpus()
_VERSIONS = sorted(version for version in _CORPUS.versions("opencode") if version in ADMITTED_OPENCODE_V2_RANGE)
_SESSIONS = ("caller_named_session", "model_session", "usage_root_session", "interrupt_root_session")
_SESSION_LISTS = ("child_sessions", "usage_child_sessions")
_MESSAGE_LISTS = (
    "prompt_messages",
    "background_root_messages",
    "compaction_messages",
    "environment_child_messages",
    "environment_root_messages",
    "interrupt_root_messages",
    "judgement_messages",
    "permission_deny_tool_messages",
    "restart_messages",
    "steer_messages",
    "takeover_messages",
)


def _fixture(version: str, name: str) -> JsonValue:
    return json.loads((_CORPUS.root / "opencode" / version / f"{name}.json").read_text())


def _items(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


@pytest.mark.parametrize("version", _VERSIONS)
def test_every_captured_session_parses(version: str) -> None:
    sessions = [parse_session(_fixture(version, name)) for name in _SESSIONS]
    children = [parse_session(item) for name in _SESSION_LISTS for item in _items(_fixture(version, name))]
    assert all(session.parent_id is None for session in sessions)
    assert children and all(child.parent_id for child in children)
    usage = parse_session(_fixture(version, "usage_root_session"))
    assert usage.outcome == "succeeded"
    assert usage.tokens.input > 0 and usage.tokens.cache_read > 0
    assert usage.model == ModelReference("openai", "gpt-6-luna", "high")
    assert parse_session(_fixture(version, "caller_named_session")).metadata["blizzard"]


@pytest.mark.parametrize("version", _VERSIONS)
def test_every_captured_message_and_part_parses(version: str) -> None:
    messages = [parse_message(item) for name in _MESSAGE_LISTS for item in _items(_fixture(version, name))]
    kinds = {message.type for message in messages}
    assert {"user", "assistant", "idle", "synthetic", "compaction"} <= kinds
    assistants = [message for message in messages if message.type == "assistant"]
    assert all(message.tokens is not None and message.cost is not None for message in assistants)
    tools = [part for message in assistants for part in message.parts if part.type == "tool"]
    assert tools and all(part.id and part.name and part.state is not None for part in tools)
    assert {part.state.status for part in tools if part.state is not None} >= {"completed", "error"}
    prompt = [parse_message(item) for item in _items(_fixture(version, "prompt_messages"))]
    assert prompt[1].final_text() == "READY"
    assert prompt[-1].outcome == "succeeded"


@pytest.mark.parametrize("version", _VERSIONS)
def test_a_subagent_part_names_its_child(version: str) -> None:
    spawn: JsonObject = {"type": "assistant", "id": "msg_x", "time": {"created": 1}, "tokens": _TOKENS, "cost": 0}
    spawn["content"] = _items(_fixture(version, "child_spawn_parts"))
    part = parse_message(spawn).parts[0]
    assert part.name == "subagent" and part.state is not None
    assert str(part.state.metadata["sessionID"]).startswith("ses_")


@pytest.mark.parametrize("version", _VERSIONS)
def test_an_interrupted_step_carries_its_error_and_the_idle_its_outcome(version: str) -> None:
    messages = [parse_message(item) for item in _items(_fixture(version, "interrupt_root_messages"))]
    errors = [message.error for message in messages if message.error is not None]
    assert errors and classify_error(errors[0]).kind is ErrorKind.INTERRUPTED
    assert messages[-1].outcome == "interrupted"


@pytest.mark.parametrize("version", _VERSIONS)
def test_every_captured_event_parses_and_names_its_session(version: str) -> None:
    events = [parse_event(item) for item in _items(_fixture(version, "events"))]
    assert events and all(event.session_id for event in events)
    assert {"session.tool.success", "session.step.ended", "session.execution.succeeded"} <= {e.type for e in events}


@pytest.mark.parametrize("version", _VERSIONS)
def test_permission_and_form_requests_parse(version: str) -> None:
    ask = parse_permission_request(_fixture(version, "permission_request"))
    assert ask.action == "shell" and ask.resources == ("touch proof-asked",)
    concurrent = [
        parse_permission_request(item) for item in _items(_fixture(version, "concurrent_permission_requests"))
    ]
    assert len({ask.session_id for ask in concurrent}) == len(concurrent)
    form = parse_form_request(_fixture(version, "form_request"))
    assert form.id.startswith("frm_") and form.title == "blizzard proof"


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("error_usage_limit", ErrorKind.USAGE_LIMIT),
        ("error_unknown_model", ErrorKind.CONFIGURATION),
        ("error_unknown_variant", ErrorKind.CONFIGURATION),
    ],
)
@pytest.mark.parametrize("version", _VERSIONS)
def test_captured_errors_classify(version: str, name: str, kind: ErrorKind) -> None:
    error = classify_error(parse_error(_fixture(version, name)))
    assert error.kind is kind
    if kind is ErrorKind.USAGE_LIMIT:
        assert error.retry_after_ms == 18748000 and error.status == 429


@pytest.mark.parametrize("version", _VERSIONS)
def test_the_source_named_error_classes_classify(version: str) -> None:
    shapes = _fixture(version, "error_shapes_from_source")
    assert isinstance(shapes, dict)
    expected = {
        "overload": ErrorKind.OVERLOAD,
        "provider_refusal": ErrorKind.REFUSAL,
        "unknown_model_or_variant": ErrorKind.CONFIGURATION,
    }
    for key, kind in expected.items():
        assert classify_error(parse_error(shapes[key])).kind is kind
    limits = shapes["usage_limit"]
    assert isinstance(limits, list)
    for limit in limits:
        assert isinstance(limit, dict)
        assert classify_error(parse_error({"type": limit["type"]})).kind is ErrorKind.USAGE_LIMIT


def test_an_overload_status_classifies_whatever_its_type() -> None:
    error = classify_error(parse_error({"type": "unknown", "message": "busy", "status": 529}))
    assert error.kind is ErrorKind.OVERLOAD and error.retryable()


@pytest.mark.parametrize("version", _VERSIONS)
def test_the_model_listing_parses_with_its_variants(version: str) -> None:
    models = [parse_listed_model(item) for item in _items(_fixture(version, "model_listing"))]
    luna = next(model for model in models if (model.provider_id, model.id) == ("openai", "gpt-6-luna"))
    assert "high" in luna.variants


_TOKENS: JsonObject = {"input": 1, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}}


@pytest.mark.parametrize(
    ("parse", "record"),
    [
        (parse_session, {"id": "ses_1", "tokens": _TOKENS, "cost": 0}),
        (parse_message, {"type": "assistant", "id": "msg_1", "time": {"created": 1}, "cost": 0}),
        (parse_message, {"type": "idle", "id": "msg_1", "time": {"created": 1}}),
        (parse_permission_request, {"id": "per_1", "sessionID": "ses_1"}),
        (parse_form_request, {"id": "frm_1"}),
        (parse_error, {"message": "no type"}),
        (parse_event, {"type": "session.created"}),
        (parse_listed_model, {"id": "m", "variants": []}),
    ],
)
def test_a_record_missing_a_read_field_is_refused(parse: object, record: dict[str, JsonValue]) -> None:
    assert callable(parse)
    with pytest.raises(OpenCodeTwoShapeError):
        parse(record)


def test_an_unread_message_or_part_type_is_kept_by_its_type() -> None:
    record: JsonObject = {
        "type": "assistant",
        "id": "msg_1",
        "time": {"created": 1},
        "tokens": _TOKENS,
        "cost": 0,
        "content": [{"type": "future-part", "whatever": 1}],
    }
    message = parse_message(record)
    assert message.parts[0].type == "future-part"
    assert parse_message({"type": "future-message", "id": "msg_2", "time": {"created": 2}}).type == "future-message"


def test_the_corpus_directory_exists() -> None:
    assert _VERSIONS and all((_CORPUS.root / "opencode" / v).is_dir() for v in _VERSIONS)
    assert Path(_CORPUS.root).is_dir()
