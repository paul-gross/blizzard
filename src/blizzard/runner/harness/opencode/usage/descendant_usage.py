"""Descendant-session step usage for one OpenCode invocation.

A step belongs to an invocation when its message was created inside the window of a ``task`` part
of that invocation's own input. Continuations of a child have disjoint windows, so the result is a
pure function of immutable export data and the input, never persisted state (``bzh:deterministic-shell``)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import adapter_model, domain_model
from blizzard.runner.harness.opencode.shapes import (
    OpenCodeMessage,
    OpenCodePart,
    OpenCodeSessionExport,
    OpenCodeShapeError,
    parse_session_export,
)
from blizzard.runner.harness.opencode.transcript.export import IOpenCodeExporter, OpenCodeExportError

_log = get_logger("blizzard.runner.harness.usage")


@adapter_model
@dataclass(frozen=True)
class DescendantStep:
    """One completed ``step-finish`` part of a descendant session, with the ``(provider, model)``
    its own message named (``None`` when the message names none)."""

    part: OpenCodePart
    provider_id: str | None
    model_id: str | None


@domain_model
@dataclass(frozen=True)
class _Window:
    start_ms: int
    end_ms: int

    def holds(self, epoch_ms: int) -> bool:
        return self.start_ms <= epoch_ms <= self.end_ms


class OpenCodeDescendantUsage:
    """Collects the steps of every descendant session an invocation's ``task`` parts spawned."""

    def __init__(self, exporter: IOpenCodeExporter) -> None:
        self._exporter = exporter

    def root_tasks(self, session_id: str) -> list[OpenCodePart]:
        """Task parts, including running ones in messages with no completed step."""
        export = self._export_of(session_id, {})
        if export is None or export.info.id != session_id:
            return []
        return [
            part
            for message in export.messages
            for part in message.parts
            if part.type == "tool" and part.tool == "task" and part.session_id == session_id
        ]

    def collect(
        self, *, root_session_id: str, task_parts: Sequence[OpenCodePart], horizon_ms: int | None
    ) -> list[DescendantStep]:
        """Every descendant step in the windows of ``task_parts`` (the invocation's own ``task``
        tool parts on the root session), deduplicated by step-finish part id. ``horizon_ms`` is the
        invocation's end; it caps every task window, including a part later completed by another
        invocation. Each distinct session is exported at most once per call. A child whose
        export fails or does not parse is logged and contributes nothing, subtree included."""
        exports: dict[str, OpenCodeSessionExport | None] = {}
        steps: dict[str, DescendantStep] = {}
        for part in task_parts:
            self._walk(root_session_id, part, horizon_ms, (root_session_id,), exports, steps)
        return list(steps.values())

    def _walk(
        self,
        parent_id: str,
        task_part: OpenCodePart,
        horizon_ms: int | None,
        ancestry: tuple[str, ...],
        exports: dict[str, OpenCodeSessionExport | None],
        steps: dict[str, DescendantStep],
    ) -> None:
        state = task_part.state
        if state is None or state.child_session_id is None:
            return
        child_id = state.child_session_id
        if child_id in ancestry:
            return
        window = self._window_of(task_part, horizon_ms)
        if window is None:
            return
        export = self._export_of(child_id, exports)
        if export is None or export.info.parent_id != parent_id:
            return
        for message in export.messages:
            created = message.info.created_at_ms
            if created is None or not window.holds(created):
                continue
            self._collect_message(message, steps)
            for part in message.parts:
                if part.type == "tool" and part.tool == "task":
                    self._walk(child_id, part, horizon_ms, (*ancestry, child_id), exports, steps)

    @staticmethod
    def _collect_message(message: OpenCodeMessage, steps: dict[str, DescendantStep]) -> None:
        for part in message.parts:
            if part.type == "step-finish" and part.tokens is not None:
                steps[part.id] = DescendantStep(part, message.info.provider_id, message.info.model_id)

    @staticmethod
    def _window_of(task_part: OpenCodePart, horizon_ms: int | None) -> _Window | None:
        state = task_part.state
        assert state is not None
        start = state.time_start_ms if state.time_start_ms is not None else 0
        end = (
            min(state.time_end_ms, horizon_ms)
            if state.time_end_ms is not None and horizon_ms is not None
            else (state.time_end_ms if state.time_end_ms is not None else horizon_ms)
        )
        if end is None:
            return None
        return _Window(start, end)

    def _export_of(
        self, session_id: str, exports: dict[str, OpenCodeSessionExport | None]
    ) -> OpenCodeSessionExport | None:
        if session_id in exports:
            return exports[session_id]
        parsed: OpenCodeSessionExport | None
        try:
            parsed = parse_session_export(json.loads(self._exporter.export(session_id)))
        except (OpenCodeExportError, json.JSONDecodeError, OpenCodeShapeError) as exc:
            _log.warning("opencode_descendant_export_unreadable", session_id=session_id, error=str(exc))
            parsed = None
        exports[session_id] = parsed
        return parsed
