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


@dataclass(frozen=True)
class Prompts:
    """The packaged prompt directory — prose shipped as files rather than inline literals,
    so a change to it stays diffable."""

    directory: Path

    def text(self, name: str) -> str:
        return (self.directory / name).read_text().rstrip("\n")


PROMPTS = Prompts(Path(__file__).resolve().parent / "prompts")

#: Layer 1's text (issue #103) — the fallback when a configured ``runner_prompt`` is unset.
DEFAULT_BLIZZARD_PREAMBLE = PROMPTS.text("blizzard_preamble.md")

#: Layers 1 and 2 both unchanged (issue #149). Carries layer 1's pointer to the facts
#: table, which layer 1's own prose would otherwise be the only thing to introduce.
RESUME_STANDING_UNCHANGED = PROMPTS.text("resume_standing_unchanged.md")

#: The raw ``{node}``/``{prior_node}`` template behind :func:`resume_cross_node` (blizzard#340).
_RESUME_CROSS_NODE = PROMPTS.text("resume_cross_node.md")


def resume_cross_node(*, node: str, prior_node: str) -> str:
    """The emit-ready role-change line for a resume whose node differs from the previous
    turn's (blizzard#340)."""
    return _RESUME_CROSS_NODE.format(node=node, prior_node=prior_node)


#: Layer 1 unchanged while layer 2 is sent in full — carrying the same facts-table
#: pointer, since the collapse rule is per layer (issue #149).
RESUME_BLIZZARD_UNCHANGED = PROMPTS.text("resume_blizzard_unchanged.md")

#: The mirror case (issue #149). No facts-table pointer: layer 1 is right there in full.
RESUME_WORKSPACE_UNCHANGED = PROMPTS.text("resume_workspace_unchanged.md")

#: A change whose new text is nothing, so it needs prose of its own: silence would read
#: as "unchanged" to a worker still holding the withdrawn policy (issue #149).
RESUME_WORKSPACE_WITHDRAWN = PROMPTS.text("resume_workspace_withdrawn.md")

#: Without it, replacement prose arrives in the same position looking exactly like the
#: block the worker was handed several spawns ago (issue #149).
RESUME_UPDATED_NOTICE = PROMPTS.text("resume_updated_notice.md")


@dataclass(frozen=True)
class Preamble:
    """The spawn prefix prepended to the node envelope prompt (issues #17, #103, #149), plus
    the fingerprint of the standing prose *this* render resolved — so a caller records what it
    sent rather than re-deriving it."""

    blizzard: str
    workspace: str
    table: str
    fingerprint: PreambleFingerprint
    prior: PreambleFingerprint | None
    node: str
    prior_node: str | None = None

    @classmethod
    def of(
        cls,
        *,
        runner_prompt: str,
        workspace_prompt: str,
        environments: Sequence[AcquiredEnvironment],
        lease_id: str,
        runner_id: str,
        chunk_id: str,
        node: str,
        prior: PreambleFingerprint | None = None,
        prior_node: str | None = None,
    ) -> Preamble:
        """``prior`` is the fingerprint of the standing prose the resumed session was last sent,
        ``None`` for a spawn resuming nothing: it selects between the full three-layer render and
        one where an unchanged layer collapses and a changed one is announced. ``node`` names this
        turn's node-step — required, so no call site can silently suppress the role-change line —
        and a ``prior_node`` known to differ from it composes that line in (blizzard#340)."""
        rows = [
            ("runner id", runner_id),
            ("chunk id", chunk_id),
            ("lease id", lease_id),
        ]
        for env in environments:
            rows.append(("environment name", env.environment_id))
            rows.append(("environment workdir", env.workdir))

        table_lines = ["| Field | Value |", "|-------|-------|"]
        table_lines += [f"| {field} | `{value}` |" for field, value in rows]

        blizzard = runner_prompt.strip() or DEFAULT_BLIZZARD_PREAMBLE
        workspace = workspace_prompt.strip()
        # The digest source, stated once: the resolved, post-`strip()` layer inputs above —
        # never the raw prompt, never the emitted text (issue #149).
        return cls(
            blizzard=blizzard,
            workspace=workspace,
            table="\n".join(table_lines),
            fingerprint=PreambleFingerprint(blizzard=cls.digest(blizzard), workspace=cls.digest(workspace)),
            prior=prior,
            node=node,
            prior_node=prior_node,
        )

    @staticmethod
    def digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @property
    def text(self) -> str:
        return "\n\n".join([*self.standing, self.table])

    @property
    def standing(self) -> list[str]:
        """Layers 1 and 2 as this spawn sends them — in full, collapsed, or announced (issue #149)."""
        # The role-change line leads every render whose nodes are known to differ (blizzard#340);
        # a differing recorded prior node already implies a resume, fingerprint or not.
        cross: list[str] = []
        if self.prior_node is not None and self.node != self.prior_node:
            cross = [resume_cross_node(node=self.node, prior_node=self.prior_node)]

        if self.prior is None:
            full = [self.blizzard, self.workspace] if self.workspace else [self.blizzard]
            return [*cross, *full]

        blizzard_held = self.prior.blizzard == self.fingerprint.blizzard
        workspace_held = self.prior.workspace == self.fingerprint.workspace

        if blizzard_held and workspace_held:
            # One line for the pair when there is a pair; an absent layer 2 is not something
            # to call "unchanged", since the fresh render never emits it either.
            return [*cross, RESUME_STANDING_UNCHANGED if self.workspace else RESUME_BLIZZARD_UNCHANGED]

        # Ahead of the notice: the announcement's "what follows supersedes" scope covers the
        # layers it introduces, and a role change is not one of them.
        layers = [*cross, RESUME_UPDATED_NOTICE]
        layers.append(RESUME_BLIZZARD_UNCHANGED if blizzard_held else self.blizzard)
        if workspace_held:
            # Only collapses when there is prose to hold in mind; an empty layer 2 is silent.
            if self.workspace:
                layers.append(RESUME_WORKSPACE_UNCHANGED)
        else:
            # Digesting the empty string rather than special-casing absence is what lands a
            # withdrawal here as an ordinary change.
            layers.append(self.workspace or RESUME_WORKSPACE_WITHDRAWN)
        return layers
