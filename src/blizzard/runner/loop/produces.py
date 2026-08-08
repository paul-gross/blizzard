"""An attempt's submissions reconciled against the node's ``produces:`` declaration."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.wire.completion import Coverage, SubmittedArtifact
from blizzard.wire.envelope import NodeEnvelope
from blizzard.wire.graph import ProducesEntry


@dataclass(frozen=True)
class ProducesReconciler:
    """The ``produces:`` specs of one node, against what its attempt has submitted."""

    envelope: NodeEnvelope

    def missing(self, git_artifacts: list[SubmittedArtifact], attachments: dict[str, str]) -> list[ProducesEntry]:
        """Every spec this attempt does not yet cover (issue #143), in declaration order.

        Evaluated by the shared :class:`Coverage` predicate, so this and the upstream
        backstop cannot drift apart.
        """
        attached = [
            SubmittedArtifact(name=name, kind=ArtifactKind.ASSET, content=content, attached=True)
            for name, content in attachments.items()
        ]
        return Coverage(git_artifacts + attached).unmet(self.envelope.node.produces)

    def nudge_message(self, missing: list[ProducesEntry]) -> str:
        """The nudge resume's message (issues #113, #143): one line per unmet spec, naming
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
        self, git_artifacts: list[SubmittedArtifact], assessment: str, attachments: dict[str, str]
    ) -> list[SubmittedArtifact]:
        """An asset artifact per produced name no git commit covers.

        An explicit attachment wins over the assessment, marked ``attached=True`` (#90).
        """
        covered = {a.name for a in git_artifacts}
        submitted: list[SubmittedArtifact] = []
        for spec in self.envelope.node.produces:
            if spec.kind is ArtifactKind.GIT_COMMIT:
                continue
            name = spec.name
            if name in covered:
                continue
            if name in attachments:
                submitted.append(
                    SubmittedArtifact(name=name, kind=ArtifactKind.ASSET, content=attachments[name], attached=True)
                )
            else:
                submitted.append(SubmittedArtifact(name=name, kind=ArtifactKind.ASSET, content=assessment))
        return submitted
