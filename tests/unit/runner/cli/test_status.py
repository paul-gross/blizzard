"""``blizzard runner status`` — the machine-local view's pure renderers and daemon-not-running report."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group
from blizzard.runner.cli.control import (
    _GateDecision,  # pyright: ignore[reportPrivateUsage]  # the decision wire shape has no public name
    _gates_line,  # pyright: ignore[reportPrivateUsage]  # the gates-line renderer has no public surface
    _UnenforceableGate,  # pyright: ignore[reportPrivateUsage]  # the flag wire shape has no public name
    gate_decision_lines,
)
from tests.runner_cli_home import (
    init_runner_home,
)


def test_status_reports_a_daemon_that_is_not_running(tmp_path: Path) -> None:
    root = init_runner_home(tmp_path)  # initialized, but nothing is serving
    result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code != 0
    assert "no runner daemon is serving" in result.output


def test_the_gates_line_annotates_each_gate_the_hub_flagged() -> None:
    flags: list[_UnenforceableGate] = [{"node": "deliver", "reason": "hub-executed"}]

    assert _gates_line(["build", "deliver"], flags) == "build, deliver (not enforceable: hub-executed)"


def test_the_gates_line_is_plain_until_a_hub_that_flags_has_answered() -> None:
    assert _gates_line(["build", "deliver"], None) == "build, deliver"
    assert _gates_line([], []) == "none"


def test_the_gate_decisions_section_lists_each_open_decision() -> None:
    decisions: list[_GateDecision] = [
        {"chunk_id": "ch_1", "node_name": "build", "submitted_at": "2026-07-16T12:00:00Z"}
    ]

    assert gate_decision_lines(decisions) == [
        "",
        "gate decisions (1):",
        "  chunk=ch_1  node=build  parked at 2026-07-16T12:00:00Z",
    ]


def test_the_gate_decisions_section_counts_none() -> None:
    assert gate_decision_lines([]) == ["", "gate decisions (0):"]


def test_the_gate_decisions_section_is_one_line_when_the_hub_could_not_answer() -> None:
    assert gate_decision_lines(None) == ["", "gate decisions: hub unreachable"]
