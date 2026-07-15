"""The runner spawn-preamble renderer (issue #17, unit tier).

The pure composition the core hands the adapter as ``prompt_prefix``: the operator's
workspace prompt above a machine-local info table naming the runner/chunk/lease identity
and — always the full held set — one name/workdir row-pair per held environment.
"""

from __future__ import annotations

import pytest

from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.preamble import render_worker_preamble


def _render(prompt: str, envs: list[AcquiredEnvironment]) -> str:
    return render_worker_preamble(
        workspace_prompt=prompt,
        environments=envs,
        lease_id="lease_1",
        runner_id="runner-local",
        chunk_id="ch_1",
    )


@pytest.mark.unit
def test_single_env_table_carries_identity_and_the_one_env() -> None:
    out = _render("You are a fleet worker.", [AcquiredEnvironment("r1", "/ws/r1")])
    # The prose is prepended above the table.
    assert out.startswith("You are a fleet worker.\n\n")
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
def test_empty_prompt_renders_table_only() -> None:
    # Absent/empty workspace prompt still injects a valid preamble — table only, no prose.
    out = _render("", [AcquiredEnvironment("r1", "/ws/r1")])
    assert out.startswith("| Field | Value |")
    assert "| winter environment name | `r1` |" in out
