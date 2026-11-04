"""What one driven turn is asked to do, handed from the adapter to the driver as a file only its owner can read: the
prompt never travels on a command line. It carries no secret — the environment a session gets is the driver's own."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.json_value import JsonObject, JsonValue, decode_json
from blizzard.runner.harness.opencode_v2.client import ModelReference, PermissionEffect, PermissionRule

#: Blizzard's fixed denies, each session's whole rule list: the native question surface no worker may reach.
FIXED_DENIES: tuple[PermissionRule, ...] = (PermissionRule("question", "*", PermissionEffect.DENY),)

METADATA_KEY = "blizzard"


class DriveKind(StrEnum):
    SPAWN = "spawn"
    RESUME = "resume"
    JUDGE = "judge"
    NUDGE = "nudge"

    def mints(self) -> bool:
        return self is DriveKind.SPAWN


class DriveRequestError(ValueError):
    """The request file is missing, unreadable, or not a request."""


@domain_model
@dataclass(frozen=True)
class DriveRequest:
    """``compaction_tokens`` is the lease's window in tokens; ``None`` leaves compaction to the operator's service."""

    kind: DriveKind
    session_id: str
    directory: str
    prompt: str
    runner_id: str
    lease_id: str
    autonomy: Autonomy
    model: ModelReference | None = None
    agent: str | None = None
    compaction_tokens: int | None = None
    rules: tuple[PermissionRule, ...] = FIXED_DENIES

    def metadata(self) -> JsonObject:
        return {METADATA_KEY: {"runner": self.runner_id, "lease": self.lease_id}}

    def minted_here(self, metadata: JsonObject) -> bool:
        """Whether a session's metadata names this request's runner as its minter."""
        mine = metadata.get(METADATA_KEY)
        return isinstance(mine, dict) and mine.get("runner") == self.runner_id

    def payload(self) -> JsonObject:
        return {
            "kind": self.kind.value,
            "session_id": self.session_id,
            "directory": self.directory,
            "prompt": self.prompt,
            "runner_id": self.runner_id,
            "lease_id": self.lease_id,
            "autonomy": self.autonomy.value,
            "model": None if self.model is None else self.model.payload(),
            "agent": self.agent,
            "compaction_tokens": self.compaction_tokens,
            "rules": [rule.payload() for rule in self.rules],
        }

    @classmethod
    def parse(cls, raw: str | bytes) -> DriveRequest:
        try:
            decoded = decode_json(raw)
            if not isinstance(decoded, dict):
                raise TypeError("not an object")
            model = decoded.get("model")
            window = decoded.get("compaction_tokens")
            agent = decoded.get("agent")
            return cls(
                kind=DriveKind(_text(decoded, "kind")),
                session_id=_text(decoded, "session_id"),
                directory=_text(decoded, "directory"),
                prompt=_text(decoded, "prompt"),
                runner_id=_text(decoded, "runner_id"),
                lease_id=_text(decoded, "lease_id"),
                autonomy=Autonomy(_text(decoded, "autonomy")),
                model=None if model is None else _model(model),
                agent=agent if isinstance(agent, str) else None,
                compaction_tokens=window if isinstance(window, int) and not isinstance(window, bool) else None,
                rules=tuple(_rule(rule) for rule in _items(decoded.get("rules"))),
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise DriveRequestError(f"not an OpenCode 2 drive request: {exc}") from exc


def _text(record: JsonObject, key: str) -> str:
    value = record[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} is not a string")
    return value


def _items(value: JsonValue) -> list[JsonObject]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise TypeError("rules is not a list of objects")
    return [item for item in value if isinstance(item, dict)]


def _model(value: JsonValue) -> ModelReference:
    if not isinstance(value, dict):
        raise TypeError("model is not an object")
    variant = value.get("variant")
    return ModelReference(_text(value, "providerID"), _text(value, "id"), variant if isinstance(variant, str) else None)


def _rule(value: JsonObject) -> PermissionRule:
    return PermissionRule(_text(value, "action"), _text(value, "resource"), PermissionEffect(_text(value, "effect")))


def write_request(directory: Path, request: DriveRequest) -> Path:
    """Write ``request`` to a new file under ``directory`` readable by its owner alone."""
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="opencode-drive-", suffix=".json", dir=directory)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(request.payload(), handle)
    return Path(name)


def take_request(path: Path) -> DriveRequest:
    """Read the request at ``path`` and remove it, so a prompt never outlives the turn that reads it."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DriveRequestError(f"cannot read the drive request: {exc}") from exc
    path.unlink(missing_ok=True)
    return DriveRequest.parse(raw)


__all__ = [
    "FIXED_DENIES",
    "METADATA_KEY",
    "DriveKind",
    "DriveRequest",
    "DriveRequestError",
    "take_request",
    "write_request",
]
