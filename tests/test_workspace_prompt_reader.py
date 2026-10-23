"""The effective workspace prompt: the store's override wins over the configured lane, an empty override included."""

from __future__ import annotations

import pytest

from blizzard.runner.harness.workspace_prompts import EffectiveWorkspacePrompt, WorkspacePromptReader

pytestmark = pytest.mark.unit


class _Overrides:
    def __init__(self, rows: dict[str, str]) -> None:
        self.rows = rows
        self.reads: list[str] = []

    def workspace_prompt_override(self, workspace_id: str) -> str | None:
        self.reads.append(workspace_id)
        return self.rows.get(workspace_id)


class _Configured:
    def prompt(self, workspace_id: str | None) -> str:
        return f"configured:{workspace_id}" if workspace_id is not None else ""


def test_an_override_wins_over_the_configured_prompt() -> None:
    reader = WorkspacePromptReader(_Overrides({"ws": "OVERRIDE"}), _Configured())

    assert reader.effective("ws") == EffectiveWorkspacePrompt(prompt="OVERRIDE", source="override")


def test_an_empty_override_is_still_an_override() -> None:
    reader = WorkspacePromptReader(_Overrides({"ws": ""}), _Configured())

    assert reader.effective("ws") == EffectiveWorkspacePrompt(prompt="", source="override")


def test_no_override_resolves_to_the_configured_prompt() -> None:
    reader = WorkspacePromptReader(_Overrides({}), _Configured())

    assert reader.effective("ws") == EffectiveWorkspacePrompt(prompt="configured:ws", source="config")


def test_no_workspace_reads_no_override() -> None:
    overrides = _Overrides({"ws": "OVERRIDE"})

    effective = WorkspacePromptReader(overrides, _Configured()).effective(None)

    assert effective == EffectiveWorkspacePrompt(prompt="", source="config")
    assert overrides.reads == []
