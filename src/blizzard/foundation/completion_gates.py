"""The two completion predicates both daemons judge a node-step by — the runner before it
submits, the hub before it accepts — kept in one home so the two can never drift apart
(``tests/test_produces_coverage_agreement.py``, ``tests/test_checks_gate_agreement.py``)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.roles import domain_model


class _SubmittedLike(Protocol):
    """Structural shape of a submitted artifact the coverage rule reads: its name, its kind,
    and whether it was explicitly attached."""

    @property
    def name(self) -> str: ...
    @property
    def kind(self) -> ArtifactKind: ...
    @property
    def attached(self) -> bool: ...


class _ProducesLike(Protocol):
    """Structural shape both ``produces:`` spec types share: a name and an artifact kind,
    nothing else."""

    @property
    def name(self) -> str: ...
    @property
    def kind(self) -> ArtifactKind: ...


@domain_model
@dataclass(frozen=True)
class Coverage:
    artifacts: Sequence[_SubmittedLike]

    @property
    def satisfied_names(self) -> set[str]:  # ast-grep-ignore: bzh:property-delegates
        """The ``produces:`` names these artifacts explicitly satisfy — an artifact with
        ``attached=True``, or a ``GIT_COMMIT`` artifact. A name present only as
        the judgement-assessment fallback is excluded (``test_produces_coverage_agreement``)."""
        return {a.name for a in self.artifacts if a.attached or a.kind == ArtifactKind.GIT_COMMIT}

    def unmet[P: _ProducesLike](self, specs: Sequence[P]) -> list[P]:
        """The ``produces:`` specs these artifacts do **not** cover.

        An ``asset`` spec is met by an artifact of **its own name** in
        :attr:`satisfied_names`; a ``git_commit`` spec is met by **any**
        ``GIT_COMMIT``-kind artifact — a *kind* match, not a name match."""
        covered_names = self.satisfied_names
        has_git_commit = any(a.kind == ArtifactKind.GIT_COMMIT for a in self.artifacts)
        unmet: list[P] = []
        for spec in specs:
            if spec.kind == ArtifactKind.GIT_COMMIT:
                if not has_git_commit:
                    unmet.append(spec)
            elif spec.name not in covered_names:
                unmet.append(spec)
        return unmet


class _HasPassed(Protocol):
    """Structural shape both check-result types share: a pass/fail verdict, nothing else."""

    @property
    def passed(self) -> bool: ...


@domain_model
@dataclass(frozen=True)
class ChecksGate:
    requires_checks: bool
    check_results: Sequence[_HasPassed]

    @property
    def violated(self) -> bool:  # ast-grep-ignore: bzh:property-delegates
        """``True`` iff a ``requires_checks`` choice is being taken while any check is red —
        the one shared home for the predicate, guarded by
        ``tests/test_checks_gate_agreement.py``. An ungated choice is never violated; a node
        with no checks records none, so the gate is vacuously satisfied."""
        return self.requires_checks and any(not r.passed for r in self.check_results)
