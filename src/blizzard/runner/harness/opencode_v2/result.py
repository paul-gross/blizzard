"""The driver's result, ``opencode-v2-driver/1``: NDJSON records, each naming its ``record`` kind, from one ``header``
to one ``result`` record. A decision is written when it is made; steps, the error, and the result once the turn ends.
A lease's capture appends attempts, so a reader reads the last one, unusable when it never reached its ``result``."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TextIO

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.json_value import JsonObject, JsonValue, decode_json
from blizzard.runner.harness.opencode_v2.errors import ErrorKind, TurnError
from blizzard.runner.harness.opencode_v2.shapes import Tokens

SCHEMA = "opencode-v2-driver/1"


class Outcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@domain_model
@dataclass(frozen=True)
class Step:
    """One assistant step's usage, in the root (``parent_id`` ``None``) or a descendant."""

    session_id: str
    parent_id: str | None
    message_id: str
    model: str | None
    tokens: Tokens
    cost: float


@domain_model
@dataclass(frozen=True)
class Decision:
    """One reply the driver gave: ``surface`` is ``permission`` or ``form``, ``reply`` what it answered."""

    surface: str
    session_id: str
    request_id: str
    reply: str
    action: str = ""
    resources: tuple[str, ...] = ()


@domain_model
@dataclass(frozen=True)
class DriverResult:
    session_id: str
    outcome: Outcome
    text: str = ""
    steps: tuple[Step, ...] = ()
    decisions: tuple[Decision, ...] = ()
    error: TurnError | None = None

    def root_model(self) -> str | None:
        return next((step.model for step in self.steps if step.parent_id is None and step.model), None)


class ResultWriter:
    """Writes one turn's records to ``out``, flushing each so a decision survives the driver's death."""

    def __init__(self, out: TextIO, session_id: str) -> None:
        self._out = out
        self._session_id = session_id
        self._write({"record": "header", "schema": SCHEMA, "session": session_id})

    def _write(self, record: JsonObject) -> None:
        self._out.write(json.dumps(record, sort_keys=True) + "\n")
        self._out.flush()

    def decision(self, decision: Decision) -> None:
        self._write(
            {
                "record": "decision",
                "surface": decision.surface,
                "session": decision.session_id,
                "request": decision.request_id,
                "reply": decision.reply,
                "action": decision.action,
                "resources": list(decision.resources),
            }
        )

    def compaction(self, context_tokens: int) -> None:
        self._write({"record": "compaction", "session": self._session_id, "context": context_tokens})

    def finish(self, outcome: Outcome, text: str, steps: Sequence[Step], error: TurnError | None) -> None:
        for step in steps:
            self._write(
                {
                    "record": "step",
                    "session": step.session_id,
                    "parent": step.parent_id,
                    "message": step.message_id,
                    "model": step.model,
                    "tokens": {
                        "input": step.tokens.input,
                        "output": step.tokens.output,
                        "reasoning": step.tokens.reasoning,
                        "cache_read": step.tokens.cache_read,
                        "cache_write": step.tokens.cache_write,
                    },
                    "cost": step.cost,
                }
            )
        if error is not None:
            self._write(
                {
                    "record": "error",
                    "kind": error.kind.value,
                    "type": error.type,
                    "message": error.message,
                    "status": error.status,
                    "retry_after_ms": error.retry_after_ms,
                }
            )
        self._write({"record": "result", "session": self._session_id, "outcome": outcome.value, "text": text})


def _str(record: JsonObject, key: str) -> str:
    value = record.get(key)
    return value if isinstance(value, str) else ""


def _int(value: JsonValue) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _step(record: JsonObject) -> Step | None:
    tokens = record.get("tokens")
    cost = record.get("cost")
    if not isinstance(tokens, dict) or not isinstance(cost, int | float) or isinstance(cost, bool):
        return None
    parent, model = record.get("parent"), record.get("model")
    return Step(
        session_id=_str(record, "session"),
        parent_id=parent if isinstance(parent, str) else None,
        message_id=_str(record, "message"),
        model=model if isinstance(model, str) else None,
        tokens=Tokens(*(_int(tokens.get(key)) or 0 for key in Tokens.__dataclass_fields__)),
        cost=float(cost),
    )


def _error(record: JsonObject) -> TurnError | None:
    try:
        kind = ErrorKind(_str(record, "kind"))
    except ValueError:
        return None
    return TurnError(
        kind=kind,
        message=_str(record, "message"),
        type=_str(record, "type"),
        status=_int(record.get("status")),
        retry_after_ms=_int(record.get("retry_after_ms")),
    )


def parse_result(output: str) -> DriverResult | None:
    """The last attempt ``output`` carries, or ``None`` when it never reached its ``result`` record — an earlier
    attempt's result is stale. Each ``header`` starts an attempt afresh; a line that is not a record is skipped, so
    stray output never costs a verdict."""
    steps: list[Step] = []
    decisions: list[Decision] = []
    error: TurnError | None = None
    last: DriverResult | None = None
    for line in output.splitlines():
        try:
            record = decode_json(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        kind = record.get("record")
        if kind == "header":
            steps, decisions, error, last = [], [], None, None
        elif kind == "step":
            step = _step(record)
            if step is not None:
                steps.append(step)
        elif kind == "decision":
            resources = record.get("resources")
            decisions.append(
                Decision(
                    surface=_str(record, "surface"),
                    session_id=_str(record, "session"),
                    request_id=_str(record, "request"),
                    reply=_str(record, "reply"),
                    action=_str(record, "action"),
                    resources=tuple(r for r in resources if isinstance(r, str)) if isinstance(resources, list) else (),
                )
            )
        elif kind == "error":
            error = _error(record)
        elif kind == "result":
            try:
                outcome = Outcome(_str(record, "outcome"))
            except ValueError:
                continue
            last = DriverResult(
                session_id=_str(record, "session"),
                outcome=outcome,
                text=_str(record, "text"),
                steps=tuple(steps),
                decisions=tuple(decisions),
                error=error,
            )
    return last


__all__ = ["SCHEMA", "Decision", "DriverResult", "Outcome", "ResultWriter", "Step", "parse_result"]
