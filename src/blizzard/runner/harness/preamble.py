"""The runner's spawn-preamble composition (issue #17, extended by issue #103).

A worker is spawned at the winter workspace root, so — unlike an interactive agent —
it has no cwd that implicitly names *which* environment(s) it holds. The runner closes
that gap by prepending a standing preamble to every node envelope, in three ordered
layers: the baked-in blizzard preamble (issue #103 — always present, naming the
`blizzard` CLI as the worker's interface), the operator-owned workspace prompt (the
deployment-specific policy layer 1 cannot know — omitted when empty), and a machine-local
info table naming the held environments and the spawn's identity. Layer 1 declares that
division of labor in its own prose (issue #147), so an operator authoring layer 2 can see
what has already been established rather than re-establishing it.

This is a pure, deterministic renderer (``bzh:deterministic-shell``): the core calls it
to build the ``prompt_prefix`` it hands the adapter, so the adapter stays dumb (it only
concatenates the prefix ahead of the envelope prompt). The environment rows are always
the **full** held set — one name/workdir pair per environment — so no environment a
multi-env chunk holds is ever invisible to the worker.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from blizzard.runner.environments.provider import AcquiredEnvironment

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

#: The baked-in blizzard preamble (issue #103) — layer 1, always present. Frames the
#: worker as operating inside the blizzard fleet-management system and names its
#: `blizzard` CLI as the interface to that system, rather than leaving a fresh install
#: with no product-level framing at all. Overridden wholesale by a configured
#: ``runner_prompt`` (:meth:`blizzard.runner.config.RunnerConfig.resolved_runner_prompt`);
#: this constant is only the fallback when that knob is unset. Packaged prose, not an
#: inline literal — the repo's convention for prompt text (``hub/graphs/<graph>/prompts/*.md``),
#: which also keeps it diffable as plain text rather than an escaped Python string.
DEFAULT_BLIZZARD_PREAMBLE = (_PROMPTS_DIR / "blizzard_preamble.md").read_text().rstrip("\n")


def render_worker_preamble(
    *,
    runner_prompt: str,
    workspace_prompt: str,
    environments: Sequence[AcquiredEnvironment],
    lease_id: str,
    runner_id: str,
    chunk_id: str,
) -> str:
    """Compose the spawn preamble prepended to the node envelope prompt (issues #17, #103).

    Three ordered layers, each owning a distinct slice of what the worker is told
    (issue #147 — the division of labor is stated in the preamble prose itself, so an
    operator authoring layer 2 can see what layer 1 has already established).

    1. The blizzard preamble: the resolved ``runner_prompt`` when non-empty, else
       :data:`DEFAULT_BLIZZARD_PREAMBLE` — never absent. Deployment-independent framing:
       the worker's fleet identity, its worker-facing ``blizzard`` CLI surface, and a
       pointer to the layer-3 table.
    2. The operator's ``workspace_prompt`` prose, omitted when empty. Everything
       deployment-specific layer 1 cannot know: workspace layout and environment
       conventions, how work is delivered, the stop conditions that end a turn. It adds
       to layer 1 rather than restating it.
    3. A machine-local facts table: the runner/chunk/lease identity, then one ``winter
       environment name`` + ``environment workdir`` row-pair per held environment (the
       single-env case is just one pair). Everything the worker needs to know which
       environment(s) to work in, now that its cwd is the workspace root.
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

    blizzard_preamble = runner_prompt.strip() or DEFAULT_BLIZZARD_PREAMBLE
    workspace_prose = workspace_prompt.strip()

    layers = [blizzard_preamble]
    if workspace_prose:
        layers.append(workspace_prose)
    layers.append(table)
    return "\n\n".join(layers)
