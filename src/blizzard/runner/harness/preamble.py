"""The runner's spawn-preamble composition (issues #17, #103, #149).

Three ordered layers: the baked-in blizzard preamble, the operator-owned workspace
prompt, and a machine-local facts table. A resumed spawn re-sends a standing layer only
when it has changed, while layer 3 is unconditional on every path. A pure, deterministic
renderer (``bzh:deterministic-shell``): every resume decision is made here, in the core."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.fingerprint import PreambleFingerprint

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass(frozen=True)
class RenderedPreamble:
    """The composed prefix, plus the fingerprint of the standing prose *this* render
    resolved — so a caller records what it sent rather than re-deriving it (issue #149)."""

    text: str
    fingerprint: PreambleFingerprint


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _prose(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text().rstrip("\n")


#: Layer 1's text (issue #103) — the fallback when a configured ``runner_prompt`` is
#: unset. Packaged prose rather than an inline literal, so it stays diffable.
DEFAULT_BLIZZARD_PREAMBLE = _prose("blizzard_preamble.md")

#: Layers 1 and 2 both unchanged (issue #149). Carries layer 1's pointer to the facts
#: table, which layer 1's own prose would otherwise be the only thing to introduce.
RESUME_STANDING_UNCHANGED = _prose("resume_standing_unchanged.md")

#: Layer 1 unchanged while layer 2 is sent in full — carrying the same facts-table
#: pointer, since the collapse rule is per layer (issue #149).
RESUME_BLIZZARD_UNCHANGED = _prose("resume_blizzard_unchanged.md")

#: The mirror case (issue #149). No facts-table pointer: layer 1 is right there in full.
RESUME_WORKSPACE_UNCHANGED = _prose("resume_workspace_unchanged.md")

#: A change whose new text is nothing, so it needs prose of its own: silence would read
#: as "unchanged" to a worker still holding the withdrawn policy (issue #149).
RESUME_WORKSPACE_WITHDRAWN = _prose("resume_workspace_withdrawn.md")

#: Without it, replacement prose arrives in the same position looking exactly like the
#: block the worker was handed several spawns ago (issue #149).
RESUME_UPDATED_NOTICE = _prose("resume_updated_notice.md")


def render_worker_preamble(
    *,
    runner_prompt: str,
    workspace_prompt: str,
    environments: Sequence[AcquiredEnvironment],
    lease_id: str,
    runner_id: str,
    chunk_id: str,
    prior: PreambleFingerprint | None = None,
) -> RenderedPreamble:
    """Compose the spawn preamble prepended to the node envelope prompt (issues #17, #103, #149).

    ``prior`` is the fingerprint of the standing prose the resumed session was last sent,
    ``None`` for a spawn resuming nothing: it selects between the full three-layer render
    and one where an unchanged layer collapses and a changed one is announced."""
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
    # The digest source, stated once: the resolved, post-`strip()` layer inputs above —
    # never the raw prompt, never the emitted text (issue #149).
    fingerprint = PreambleFingerprint(blizzard=_digest(blizzard_preamble), workspace=_digest(workspace_prose))

    layers = _standing_layers(blizzard_preamble, workspace_prose, prior=prior, current=fingerprint)
    layers.append(table)
    return RenderedPreamble(text="\n\n".join(layers), fingerprint=fingerprint)


def _standing_layers(
    blizzard_preamble: str,
    workspace_prose: str,
    *,
    prior: PreambleFingerprint | None,
    current: PreambleFingerprint,
) -> list[str]:
    """Layers 1 and 2 as this spawn sends them — in full, collapsed, or announced (issue #149)."""
    if prior is None:
        return [blizzard_preamble, workspace_prose] if workspace_prose else [blizzard_preamble]

    blizzard_held = prior.blizzard == current.blizzard
    workspace_held = prior.workspace == current.workspace

    if blizzard_held and workspace_held:
        # One line for the pair when there is a pair; an absent layer 2 is not something
        # to call "unchanged", since the fresh render never emits it either.
        return [RESUME_STANDING_UNCHANGED if workspace_prose else RESUME_BLIZZARD_UNCHANGED]

    layers = [RESUME_UPDATED_NOTICE]
    layers.append(RESUME_BLIZZARD_UNCHANGED if blizzard_held else blizzard_preamble)
    if workspace_held:
        # Only collapses when there is prose to hold in mind; an empty layer 2 is silent.
        if workspace_prose:
            layers.append(RESUME_WORKSPACE_UNCHANGED)
    else:
        # Digesting the empty string rather than special-casing absence is what lands a
        # withdrawal here as an ordinary change.
        layers.append(workspace_prose or RESUME_WORKSPACE_WITHDRAWN)
    return layers
