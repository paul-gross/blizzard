"""The runner's spawn-preamble composition (issue #17, D-063).

A worker is spawned at the winter workspace root, so — unlike an interactive agent —
it has no cwd that implicitly names *which* environment(s) it holds. The runner closes
that gap by prepending a standing preamble to every node envelope: the operator-owned
workspace prompt (the "you are a fleet worker in this winter workspace" framing) followed
by a machine-local info table naming the held environments and the spawn's identity.

This is a pure, deterministic renderer (``bzh:deterministic-shell``): the core calls it
to build the ``prompt_prefix`` it hands the adapter, so the adapter stays dumb (it only
concatenates the prefix ahead of the envelope prompt). The environment rows are always
the **full** held set — one name/workdir pair per environment — so no environment a
multi-env chunk holds is ever invisible to the worker.
"""

from __future__ import annotations

from collections.abc import Sequence

from blizzard.runner.environments.provider import AcquiredEnvironment


def render_worker_preamble(
    *,
    workspace_prompt: str,
    environments: Sequence[AcquiredEnvironment],
    lease_id: str,
    runner_id: str,
    chunk_id: str,
) -> str:
    """Compose the spawn preamble prepended to the node envelope prompt (issue #17).

    The operator's ``workspace_prompt`` prose (omitted when empty — a table-only
    injection) above a machine-local facts table: the runner/chunk/lease identity, then
    one ``winter environment name`` + ``environment workdir`` row-pair per held
    environment (the single-env case is just one pair). Everything the worker needs to
    know which environment(s) to work in, now that its cwd is the workspace root.
    """
    rows = [
        ("runner id", runner_id),
        ("chunk id", chunk_id),
        ("lease id", lease_id),
    ]
    for env in environments:
        rows.append(("winter environment name", env.environment_id))
        rows.append(("environment workdir", env.workdir))

    table_lines = ["| Field | Value |", "|-------|-------|"]
    table_lines += [f"| {field} | `{value}` |" for field, value in rows]
    table = "\n".join(table_lines)

    prose = workspace_prompt.strip()
    if prose:
        return f"{prose}\n\n{table}"
    return table
