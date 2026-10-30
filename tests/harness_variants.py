from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

import pytest

from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID

MOCK_CLAUDE_CODE_MODEL = "claude-opus-4-8"
OPENCODE_SESSION_MODEL = "mock-provider/opencode-e2e"


@dataclass(frozen=True)
class MockHarness:
    param: str
    harness_id: str
    response_model: str
    request_model: str | None

    def graph(self, graph: dict[str, Any]) -> dict[str, Any]:
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
        flag = "--resume" if self.harness_id == CLAUDE_CODE_HARNESS_ID else "--session"
        match = re.search(rf"{flag} (\S+)", takeover)
        assert match is not None, f"takeover command carries no {flag} session: {takeover!r}"
        return match.group(1)


CLAUDE_CODE = MockHarness("claude-code", CLAUDE_CODE_HARNESS_ID, MOCK_CLAUDE_CODE_MODEL, None)
OPENCODE = MockHarness("opencode", OPENCODE_HARNESS_ID, OPENCODE_SESSION_MODEL, OPENCODE_SESSION_MODEL)

both_mock_harnesses = pytest.mark.parametrize("harness", [CLAUDE_CODE, OPENCODE], ids=lambda h: h.param)
