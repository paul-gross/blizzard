"""The decoding of an external-subscription usage fact's payload, shared by the runner lane and the host lane."""

from __future__ import annotations

import json
from typing import TypeGuard

from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.execution.payload import Payload
from blizzard.hub.domain.runners.registration import ExternalSubscriptionUsageWindow

_log = get_logger("blizzard.hub.usage_facts")


def _is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def external_usage_windows_json(raw: object, *, owner_id: str, slug: str) -> str:
    """The complete usage windows from a fact; malformed entries are omitted at intake.

    Instants are normalized to UTC here, so a naive or offset ``resets_at`` is stored
    already-UTC rather than repaired on every later read (``bzh:utc-instants``)."""
    if not _is_list(raw):
        _log.warning("external usage windows not a list", owner_id=owner_id, slug=slug)
        return "[]"
    windows: list[dict[str, object]] = []
    for entry in raw:
        window = ExternalSubscriptionUsageWindow.admitted(entry)
        if isinstance(window, str):
            _log.warning("dropped malformed external usage window", owner_id=owner_id, slug=slug, reason=window)
            continue
        windows.append(window.stored)
    return json.dumps(windows)


def subscription_identity(fact: Payload) -> tuple[str, str] | None:
    """An external-subscription usage fact's ``(slug, name)`` — ``None`` without a non-empty
    string slug; the name defaults to the slug."""
    slug = fact.get("slug")
    if not isinstance(slug, str) or not slug:
        return None
    return slug, fact.text("name") or slug


def usage_miss_reason(fact: Payload) -> str | None:
    """A usage miss's reason, kept raw whether or not the hub recognizes it; ``None`` unless a non-blank string."""
    reason = fact.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return None
    return reason
