"""The two mock harnesses a single-harness e2e scenario runs under, as one pytest parameter.

``claude-code`` leaves a graph as written: the runner's configured default harness works every node. ``opencode``
moves every runner node onto a graph-level session pinned to ``harnesses: [opencode]`` with a declared model, so
each fresh mint is a real OpenCode dispatch. A node declared ``fresh`` opens its own pool with ``fresh:``; any other
node resumes its own pool, so a re-entered node continues the session it started, as it does under Claude Code.

Scripts run under either harness must capture every child process's stdout: ``mock-opencode`` streams its JSONL
event wire on the worker's stdout, and an uncaptured child would write into it."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

import pytest

from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID

#: The model every ``mock-claude-code`` usage record reports, whatever ``--model`` it was launched with
#: (``blizzard-mock``'s ``harness/facades/_usage.py::MOCK_MODEL``).
MOCK_CLAUDE_CODE_MODEL = "claude-opus-4-8"
#: A strict ``provider/model`` reference — the only non-tier form OpenCode resolves — and so what the pinned
#: session's lease resolves, the mock records, and its usage reports.
OPENCODE_SESSION_MODEL = "mock-provider/opencode-e2e"


@dataclass(frozen=True)
class MockHarness:
    """One mock harness: the runner's id for it, the model its usage reports, and the model its pinned session
    declares (``None`` where the scenario declares none)."""

    param: str
    harness_id: str
    response_model: str
    request_model: str | None

    def graph(self, graph: dict[str, Any]) -> dict[str, Any]:
        """``graph`` with every runner node pinned to this harness."""
        if self.harness_id == CLAUDE_CODE_HARNESS_ID:
            return graph
        pinned = copy.deepcopy(graph)
        sessions = pinned.setdefault("sessions", {})
        for name, node in pinned["nodes"].items():
            if node.get("executor") != "runner":
                continue
            pool = f"{self.param}-{name}"
            sessions[pool] = {"harnesses": [self.harness_id], "model": [self.request_model]}
            node["session"] = f"fresh:{pool}" if node.get("session") == "fresh" else f"resume:{pool}"
        return pinned

    def graph_yaml(self, graph: dict[str, Any]) -> str:
        import yaml

        return yaml.safe_dump(self.graph(graph), sort_keys=False)

    def takeover_session(self, takeover: str) -> str:
        """The session id an escalation's pasteable takeover command resumes."""
        flag = "--resume" if self.harness_id == CLAUDE_CODE_HARNESS_ID else "--session"
        match = re.search(rf"{flag} (\S+)", takeover)
        assert match is not None, f"takeover command carries no {flag} session: {takeover!r}"
        return match.group(1)


CLAUDE_CODE = MockHarness("claude-code", CLAUDE_CODE_HARNESS_ID, MOCK_CLAUDE_CODE_MODEL, None)
OPENCODE = MockHarness("opencode", OPENCODE_HARNESS_ID, OPENCODE_SESSION_MODEL, OPENCODE_SESSION_MODEL)

#: Parametrizes a scenario's ``harness`` argument over both mock harnesses.
both_mock_harnesses = pytest.mark.parametrize("harness", [CLAUDE_CODE, OPENCODE], ids=lambda h: h.param)
