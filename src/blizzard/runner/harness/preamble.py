"""The runner's spawn-preamble composition (issue #17, extended by issues #103, #149).

A worker is spawned at the winter workspace root, so — unlike an interactive agent —
it has no cwd that implicitly names *which* environment(s) it holds. The runner closes
that gap by prepending a standing preamble to every node envelope, in three ordered
layers: the baked-in blizzard preamble (issue #103 — naming the `blizzard` CLI as the
worker's interface), the operator-owned workspace prompt (the deployment-specific policy
layer 1 cannot know — omitted when empty), and a machine-local info table naming the held
environments and the spawn's identity. Layer 1 declares that division of labor in its own
prose (issue #147), so an operator authoring layer 2 can see what has already been
established rather than re-establishing it.

**A fresh spawn renders all three layers in full. A spawn that resumes an existing session
does not** (issue #149). Layers 1 and 2 are *standing* prose the session already holds, so
a resumed spawn sends one only when it has actually changed since that session was last
spawned — collapsing an unchanged layer to a one-line statement that it still applies, and
leading a changed one with an explicit updated-since-your-previous-turn announcement. The
caller supplies the ``prior`` :class:`PreambleFingerprint` that makes the comparison
possible; ``prior=None`` (a fresh session, a ``session: fresh`` node, or a session with
nothing recorded) is the full three-layer render, byte-for-byte as before.

**Layer 3 is unconditional on every path.** The facts table is re-rendered per attempt
around a freshly minted ``lease_id``, and a worker whose table names a dead lease cannot
address the fleet — so it is never a comparison input and never elided. Layer 1's pointer
to it travels with layer 1: whenever layer 1 collapses, its collapse line still introduces
the table, so the worker is never handed an unintroduced markdown grid.

This is a pure, deterministic renderer (``bzh:deterministic-shell``): the core calls it
to build the ``prompt_prefix`` it hands the adapter, so the adapter stays dumb (it only
concatenates the prefix ahead of the envelope prompt) and every resume decision stays in
the core. The environment rows are always the **full** held set — one name/workdir pair
per environment — so no environment a multi-env chunk holds is ever invisible to the
worker.
"""

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
    """What :func:`render_worker_preamble` returns (issue #149): the composed prefix, plus
    the fingerprint of the standing prose *this* render resolved.

    The fingerprint rides along so the caller records what it actually sent rather than
    re-deriving the digests by hand off inputs it would have to keep in step — one
    statement of the digest's source, in the renderer that owns the resolution.
    """

    text: str
    fingerprint: PreambleFingerprint


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _prose(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text().rstrip("\n")


#: The baked-in blizzard preamble (issue #103) — layer 1's text. Frames the
#: worker as operating inside the blizzard fleet-management system and names its
#: `blizzard` CLI as the interface to that system, rather than leaving a fresh install
#: with no product-level framing at all. Overridden wholesale by a configured
#: ``runner_prompt`` (:meth:`blizzard.runner.config.RunnerConfig.resolved_runner_prompt`);
#: this constant is only the fallback when that knob is unset. Sent in full on every fresh
#: spawn and on any resume whose layer 1 moved; a resume that already holds it gets
#: :data:`RESUME_BLIZZARD_UNCHANGED` instead (issue #149). Packaged prose, not an
#: inline literal — the repo's convention for prompt text (``hub/graphs/<graph>/prompts/*.md``),
#: which also keeps it diffable as plain text rather than an escaped Python string.
DEFAULT_BLIZZARD_PREAMBLE = _prose("blizzard_preamble.md")

#: Layers 1 and 2 both unchanged on a resumed spawn — the single line that replaces them
#: (issue #149). Carries layer 1's pointer to the facts table, which layer 1's own prose
#: would otherwise have been the only thing to introduce.
RESUME_STANDING_UNCHANGED = _prose("resume_standing_unchanged.md")

#: Layer 1 unchanged while layer 2 is sent in full — the common resume-with-a-changed-
#: workspace-prompt shape (issue #149). Carries the same facts-table pointer as
#: :data:`RESUME_STANDING_UNCHANGED`, because the rule is per layer: layer 1 can collapse
#: while its sibling does not, and the table still needs introducing.
RESUME_BLIZZARD_UNCHANGED = _prose("resume_blizzard_unchanged.md")

#: Layer 2 unchanged while layer 1 is sent in full (issue #149) — the mirror case,
#: reachable across a runner restart that changed ``runner_prompt`` or upgraded the
#: packaged default. No facts-table pointer: that is layer 1's to carry, and layer 1 is
#: right there in full.
RESUME_WORKSPACE_UNCHANGED = _prose("resume_workspace_unchanged.md")

#: Layer 2 withdrawn — an operator replaced a non-empty workspace prompt with an empty one
#: (issue #149). A change whose new text is nothing, so it needs prose of its own: silence
#: would read as "unchanged" to a worker that still holds the withdrawn policy.
RESUME_WORKSPACE_WITHDRAWN = _prose("resume_workspace_withdrawn.md")

#: The announcement leading any resumed spawn where a standing layer moved (issue #149).
#: The correctness half of the change: without it, replacement prose arrives in the same
#: position looking exactly like the block the worker was handed several spawns ago.
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

    Three ordered layers, each owning a distinct slice of what the worker is told
    (issue #147 — the division of labor is stated in the preamble prose itself, so an
    operator authoring layer 2 can see what layer 1 has already established).

    1. The blizzard preamble: the resolved ``runner_prompt`` when non-empty, else
       :data:`DEFAULT_BLIZZARD_PREAMBLE`. Deployment-independent framing: the worker's
       fleet identity, its worker-facing ``blizzard`` CLI surface, and a pointer to the
       layer-3 table.
    2. The operator's ``workspace_prompt`` prose, omitted when empty. Everything
       deployment-specific layer 1 cannot know: workspace layout and environment
       conventions, how work is delivered, the stop conditions that end a turn. It adds
       to layer 1 rather than restating it.
    3. A machine-local facts table: the runner/chunk/lease identity, then one ``winter
       environment name`` + ``environment workdir`` row-pair per held environment (the
       single-env case is just one pair). Everything the worker needs to know which
       environment(s) to work in, now that its cwd is the workspace root.

    ``prior`` is the fingerprint of the standing prose the session being resumed was last
    sent, or ``None`` for a spawn that resumes nothing. It selects between two shapes:

    * ``prior is None`` — all three layers in full, byte-for-byte as before issue #149.
    * ``prior`` given — layers 1 and 2 are compared against it. An unchanged layer
      collapses to its one-line statement that it still applies; a changed one is sent in
      full behind :data:`RESUME_UPDATED_NOTICE`, so replacement prose can never arrive
      looking like the block the worker was handed several spawns ago. Layer 3 follows in
      full either way.

    The returned :class:`RenderedPreamble` carries the fingerprint of the prose **this**
    render resolved — always taken over the layers' effective, post-resolution,
    post-``strip()`` input text, never over what was emitted. Digesting the *output* would
    poison the elided path: the recorded fingerprint would be derived from a collapse line,
    the next resume would compare it against the real resolved prose, find a mismatch, and
    announce an update ahead of prose that never changed.
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
    # The digest source, stated once: the resolved layer inputs, exactly the two strings
    # computed above. Digesting raw `runner_prompt` would leave layer 1's digest unmoved
    # when an upgrade edits the packaged default with the knob unset — the default
    # deployment, and the likelier of layer 1's two doors — and digesting unstripped text
    # would let a whitespace-only `PUT /api/workspace-prompt` fire a false alarm on the
    # very signal this exists to make trustworthy.
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
        # Nothing moved. One line for the pair when there is a pair to speak for; a
        # deployment with no workspace prompt has only layer 1 to collapse, and saying its
        # absent layer 2 is "unchanged" would be noise the fresh render never emits either.
        return [RESUME_STANDING_UNCHANGED if workspace_prose else RESUME_BLIZZARD_UNCHANGED]

    layers = [RESUME_UPDATED_NOTICE]
    layers.append(RESUME_BLIZZARD_UNCHANGED if blizzard_held else blizzard_preamble)
    if workspace_held:
        # An unchanged layer 2 still collapses behind a changed layer 1 — but only when
        # there is prose to hold in mind; an always-empty layer 2 stays silent.
        if workspace_prose:
            layers.append(RESUME_WORKSPACE_UNCHANGED)
    else:
        # Digesting the empty string rather than special-casing absence is what makes the
        # withdrawal land here as an ordinary change: its new text is nothing, so silence
        # would read as "unchanged" to a worker still holding the withdrawn policy.
        layers.append(workspace_prose or RESUME_WORKSPACE_WITHDRAWN)
    return layers
