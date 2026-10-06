"""An attempt's submissions reconciled against the node's ``produces:`` declaration."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.completion_gates import Coverage
from blizzard.foundation.roles import domain_model
from blizzard.runner.node_steps.envelope import Envelope, ProducesSpec
from blizzard.runner.node_steps.submissions import CompletionArtifact


@domain_model
@dataclass(frozen=True)
class ProducesReconciler:
    """The ``produces:`` specs of one node, against what its attempt has submitted."""

    envelope: Envelope

    def missing(self, git_artifacts: list[CompletionArtifact], attached_names: Iterable[str]) -> list[ProducesSpec]:
        """Every spec this attempt does not yet cover, in declaration order. An attached
        name's artifact carries empty content, since coverage is judged on names alone."""
        attached = [
            CompletionArtifact(name=name, kind=ArtifactKind.ASSET, content="", attached=True) for name in attached_names
        ]
        return Coverage(git_artifacts + attached).unmet(self.envelope.node.produces)

    def nudge_message(self, missing: list[ProducesSpec]) -> str:
        """The nudge resume's message: one line per unmet spec, naming
        the kind-appropriate declaration verb. Same inert ``#`` framing as the resume messages.
        """
        lines = ["# This node's `produces:` still needs an explicit submission:"]
        for spec in missing:
            if spec.kind is ArtifactKind.GIT_COMMIT:
                lines.append(
                    f"#   - {spec.name} (git_commit): push your branch, then run "
                    f"`blizzard runner artifact commit --repo <repo> --branch <branch> "
                    f"--commit <sha>` for each repo you touched (`<repo>` is its name in "
                    f"the environment's manifest; add `--env <id>` when the chunk holds "
                    f"more than one environment)."
                )
            else:
                lines.append(
                    f"#   - {spec.name} (asset): run `blizzard runner artifact create "
                    f"--name {spec.name}` with the content on stdin."
                )
        lines.append("# Do this before this attempt is judged done.")
        return "\n".join(lines)

    def collect_assets(
        self, git_artifacts: list[CompletionArtifact], assessment: str, attachments: dict[str, str]
    ) -> list[CompletionArtifact]:
        """An asset artifact per produced name no git commit covers.

        An explicit attachment wins over the assessment, marked ``attached=True``.
        """
        covered = {a.name for a in git_artifacts}
        submitted: list[CompletionArtifact] = []
        for spec in self.envelope.node.produces:
            if spec.kind is ArtifactKind.GIT_COMMIT:
                continue
            name = spec.name
            if name in covered:
                continue
            if name in attachments:
                submitted.append(
                    CompletionArtifact(name=name, kind=ArtifactKind.ASSET, content=attachments[name], attached=True)
                )
            else:
                submitted.append(CompletionArtifact(name=name, kind=ArtifactKind.ASSET, content=assessment))
        return submitted
