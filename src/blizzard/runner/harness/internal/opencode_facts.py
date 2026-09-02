"""What one parsed OpenCode event stream, export, or runner-owned config does and does not show.

The probe asks these questions of shapes it already holds, so they stay free functions that start
no process and reach no network.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from blizzard.runner.harness.internal.opencode_proof_script import (
    PERMISSION_DENIAL_MESSAGES,
    PERMISSION_TOOL,
)
from blizzard.runner.harness.internal.opencode_shapes import (
    OpenCodeRunEvent,
    OpenCodeSessionExport,
    parse_worker_config,
)
from blizzard.runner.harness.internal.opencode_transcript import TranscriptExportSample, TranscriptProof

# Quota, billing, and auth refusals are the provider's answer, not OpenCode's.
_PROVIDER_REFUSAL_STATUSES = frozenset({401, 402, 403, 429})


def provider_refusal(events: Sequence[OpenCodeRunEvent] | None) -> str | None:
    """Name a provider-side refusal; its status is the only detail safe to retain."""

    for event in events or ():
        error = event.error
        if error is not None and error.status_code in _PROVIDER_REFUSAL_STATUSES:
            return f"the provider refused the request with status {error.status_code}"
    return None


def matching_permission_calls(
    events: Sequence[OpenCodeRunEvent], command: str, *, tool: str = PERMISSION_TOOL
) -> list[OpenCodeRunEvent]:
    """Return exactly the tool requests for one command, including their terminal state."""

    return [
        event
        for event in events
        if event.part is not None
        and event.part.type == "tool"
        and event.part.tool == tool
        and event.part.state is not None
        and event.part.state.input.get("command") == command
    ]


def is_explicit_permission_denial(event: OpenCodeRunEvent, command: str) -> bool:
    state = event.part.state if event.part is not None else None
    if state is None or state.status != "error" or state.error is None:
        return False
    error = state.error.strip().lower()
    if error in PERMISSION_DENIAL_MESSAGES:
        return True
    prefix = "the user has specified a rule which prevents you from using this specific tool call. "
    marker = "here are some of the relevant rules "
    if not error.startswith(prefix + marker):
        return False
    raw_rules = state.error.strip()[len(prefix + marker) :]
    try:
        rules = json.loads(raw_rules)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return isinstance(rules, list) and any(
        isinstance(rule, Mapping)
        and rule.get("permission") == PERMISSION_TOOL
        and rule.get("pattern") == command
        and rule.get("action") == "deny"
        for rule in rules
    )


def runner_config_denies(env: Mapping[str, str], command: str) -> bool:
    path = env.get("OPENCODE_CONFIG")
    if not path:
        return False
    try:
        config = parse_worker_config(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    bash = config.permissions.get(PERMISSION_TOOL)
    return isinstance(bash, Mapping) and bash.get(command) == "deny"


def has_exact_permission_denial(
    events: Sequence[OpenCodeRunEvent], *, command: str, tool: str = PERMISSION_TOOL
) -> bool:
    """Classify one terminal explicit denial from a captured denied-tool shape.

    ``run --format json`` may emit pending/running/error updates for one part. They share identity
    and command; only a second terminal denial is a duplicate.
    """

    matching = matching_permission_calls(events, command, tool=tool)
    terminal = [
        event
        for event in matching
        if event.part is not None and event.part.state is not None and event.part.state.status in {"completed", "error"}
    ]
    return len(terminal) == 1 and is_explicit_permission_denial(terminal[0], command)


def has_live_tool_state(export: OpenCodeSessionExport, command: str | None = None) -> bool:
    return any(
        part.state is not None
        and part.state.status in {"pending", "running"}
        and (command is None or part.state.input.get("command") == command)
        for message in export.messages
        for part in message.parts
    )


def has_takeover_prompt(export: OpenCodeSessionExport, prompt: str) -> bool:
    return any(
        message.info.role == "user" and any(part.type == "text" and part.text == prompt for part in message.parts)
        for message in export.messages
    )


def transcript_evidence(samples: Sequence[TranscriptExportSample], transcript: TranscriptProof) -> dict[str, object]:
    """Retain identities and state labels without retaining model or tool text."""

    payload = transcript.to_payload()
    payload["samples"] = [
        {
            "name": sample.name,
            "live": sample.live,
            "phase": sample.phase,
            "session_id": sample.export.info.id,
            "messages": [
                {
                    "message_id": message.info.id,
                    "role": message.info.role,
                    "parts": [
                        {
                            "part_id": part.id,
                            "type": part.type,
                            "state": part.state.status if part.state is not None else None,
                        }
                        for part in message.parts
                    ],
                }
                for message in sample.export.messages
            ],
        }
        for sample in samples
    ]
    return payload


def has_requested_model_variant(export: OpenCodeSessionExport, provider: str, model: str, variant: str) -> bool:
    return any(
        has_requested_model_variant_for_message(export, message.info.id, provider, model, variant)
        for message in export.messages
    )


def has_requested_model_variant_for_message(
    export: OpenCodeSessionExport, message_id: str, provider: str, model: str, variant: str
) -> bool:
    return any(
        message.info.id == message_id
        and message.info.role == "assistant"
        and message.info.provider_id == provider
        and message.info.model_id == model
        and message.info.variant == variant
        for message in export.messages
    )


__all__ = [
    "has_exact_permission_denial",
    "has_live_tool_state",
    "has_requested_model_variant",
    "has_requested_model_variant_for_message",
    "has_takeover_prompt",
    "is_explicit_permission_denial",
    "matching_permission_calls",
    "provider_refusal",
    "runner_config_denies",
    "transcript_evidence",
]
