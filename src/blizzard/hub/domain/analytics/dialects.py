"""Analytics dialect registry (blizzard#439) — the recognition registry keyed
by a segment's own exact ``normalizer_version``. A kind absent from a dialect
derives zero events of that kind; a version absent from :data:`DIALECTS`
derives zero events at all (D1, D9 — ``bzh:domain-core``). Version keys stay
string literals, never imported from ``blizzard.runner``: a normalizer's own
version constant is that module's business, not this registry's dependency."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.hub.domain.analytics.events import KIND_AGENT_SPAWN, KIND_FILE_READ, KIND_SKILL_INVOCATION


@dataclass(frozen=True)
class DialectEntry:
    """One kind's recognition parameters for one dialect: the tool name that
    kind's calls arrive as, and the argument key carrying its subject."""

    tool_name: str
    argument_key: str


#: Claude Code's own normalizer stamp (blizzard#327), now data rather than a name-only mapping.
_CLAUDE_CODE_JSONL_2: dict[str, DialectEntry] = {
    KIND_FILE_READ: DialectEntry(tool_name="Read", argument_key="file_path"),
    KIND_SKILL_INVOCATION: DialectEntry(tool_name="Skill", argument_key="skill"),
    KIND_AGENT_SPAWN: DialectEntry(tool_name="Agent", argument_key="subagent_type"),
}

#: OpenCode's spawn entry only (D5) — fixture-proven; read/skill have no proven tool name yet.
_OPENCODE_EXPORT_1: dict[str, DialectEntry] = {
    KIND_AGENT_SPAWN: DialectEntry(tool_name="task", argument_key="agent"),
}

#: Every registered dialect, keyed by the segment's own exact normalizer_version.
DIALECTS: dict[str, dict[str, DialectEntry]] = {
    "claude-code-jsonl/2": _CLAUDE_CODE_JSONL_2,
    "opencode-export/1": _OPENCODE_EXPORT_1,
}
