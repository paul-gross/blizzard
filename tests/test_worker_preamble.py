"""The runner spawn-preamble renderer (issues #17, #103, unit tier).

The pure composition the core hands the adapter as ``prompt_prefix``: the baked-in
blizzard preamble (or its ``runner_prompt`` override) above the operator's workspace
prompt above a machine-local info table naming the runner/chunk/lease identity and —
always the full held set — one name/workdir row-pair per held environment.
"""

from __future__ import annotations

import pytest

from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.preamble import DEFAULT_BLIZZARD_PREAMBLE, render_worker_preamble


def _render(
    workspace_prompt: str,
    envs: list[AcquiredEnvironment],
    *,
    runner_prompt: str = "",
) -> str:
    return render_worker_preamble(
        runner_prompt=runner_prompt,
        workspace_prompt=workspace_prompt,
        environments=envs,
        lease_id="lease_1",
        runner_id="runner-local",
        chunk_id="ch_1",
    )


@pytest.mark.unit
def test_single_env_table_carries_identity_and_the_one_env() -> None:
    out = _render("You are a fleet worker.", [AcquiredEnvironment("r1", "/ws/r1")])
    # The blizzard preamble leads (the baked default, since runner_prompt is unset).
    assert out.startswith(DEFAULT_BLIZZARD_PREAMBLE)
    # The workspace prose layers between the blizzard preamble and the facts table.
    assert "\n\nYou are a fleet worker.\n\n" in out
    # Machine-local identity rows.
    assert "| runner id | `runner-local` |" in out
    assert "| chunk id | `ch_1` |" in out
    assert "| lease id | `lease_1` |" in out
    # The single environment appears as a name/workdir pair.
    assert "| winter environment name | `r1` |" in out
    assert "| environment workdir | `/ws/r1` |" in out


@pytest.mark.unit
def test_multi_env_table_names_every_held_environment() -> None:
    out = _render(
        "prose",
        [AcquiredEnvironment("r1", "/ws/r1"), AcquiredEnvironment("r2", "/ws/r2")],
    )
    # Both held environments appear — never just the first (issue #17).
    assert "| winter environment name | `r1` |" in out
    assert "| environment workdir | `/ws/r1` |" in out
    assert "| winter environment name | `r2` |" in out
    assert "| environment workdir | `/ws/r2` |" in out
    # One row-pair per env: two name rows, two workdir rows.
    assert out.count("| winter environment name |") == 2
    assert out.count("| environment workdir |") == 2


@pytest.mark.unit
def test_empty_workspace_prompt_omits_that_layer() -> None:
    # Absent/empty workspace prompt omits layer 2 — the blizzard preamble and the
    # table still compose, back to back, with no workspace-prose layer between them.
    out = _render("", [AcquiredEnvironment("r1", "/ws/r1")])
    assert out == f"{DEFAULT_BLIZZARD_PREAMBLE}\n\n| Field | Value |\n|-------|-------|\n" + (
        "| runner id | `runner-local` |\n"
        "| chunk id | `ch_1` |\n"
        "| lease id | `lease_1` |\n"
        "| winter environment name | `r1` |\n"
        "| environment workdir | `/ws/r1` |"
    )


@pytest.mark.unit
def test_baked_default_used_when_runner_prompt_unset() -> None:
    out = _render("", [AcquiredEnvironment("r1", "/ws/r1")])
    assert out.startswith(DEFAULT_BLIZZARD_PREAMBLE)
    assert "blizzard runner ask" in out
    assert "blizzard runner pm-items" in out
    assert "blizzard runner heartbeat" in out
    assert "blizzard runner session-end" in out
    # Names the worker verbs explicitly rather than pointing at the operator's
    # `--help` menu, which also lists mutating verbs (`requeue`, `takeover`, ...)
    # a worker should never run.
    assert "blizzard runner --help" not in out


@pytest.mark.unit
def test_runner_prompt_overrides_the_baked_default() -> None:
    out = _render("", [AcquiredEnvironment("r1", "/ws/r1")], runner_prompt="Custom blizzard framing.")
    assert out.startswith("Custom blizzard framing.\n\n")
    assert DEFAULT_BLIZZARD_PREAMBLE not in out


@pytest.mark.unit
def test_runner_prompt_layers_ahead_of_workspace_prompt_ahead_of_table() -> None:
    out = _render("Workspace-specific prose.", [AcquiredEnvironment("r1", "/ws/r1")], runner_prompt="Blizzard prose.")
    assert out == (
        "Blizzard prose.\n\n"
        "Workspace-specific prose.\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| runner id | `runner-local` |\n"
        "| chunk id | `ch_1` |\n"
        "| lease id | `lease_1` |\n"
        "| winter environment name | `r1` |\n"
        "| environment workdir | `/ws/r1` |"
    )
