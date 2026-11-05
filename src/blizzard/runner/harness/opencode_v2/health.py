"""The OpenCode 2 health-probe binding: the binary, its admitted range, and whether the binary and the service it
manages are one major version."""

from __future__ import annotations

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.health import DeclaredDegradation
from blizzard.runner.harness.identity import OPENCODE_HARNESS_ID
from blizzard.runner.harness.internal.version_probe import VersionProbe
from blizzard.runner.harness.offline_compatibility import (
    CorpusConfigurationError,
    ICompatibilityCorpus,
    assert_admitted_range_has_corpus,
)
from blizzard.runner.harness.opencode_v2.service import IServiceKeeper
from blizzard.runner.harness.opencode_v2.version import (
    ADMITTED_OPENCODE_V2_RANGE,
    ADMITTED_OPENCODE_V2_RANGE_TEXT,
    normalize_opencode_v2_version,
)
from blizzard.runner.harness.opencode_v2.version_hold import VersionHold

_log = get_logger("blizzard.runner.harness.opencode")


class OpenCodeTwoHealthProbe:
    """The OpenCode 2 binding's :class:`~blizzard.runner.harness.adapter.IHarnessHealthProbe`. Reports evidence, never
    decides availability."""

    def __init__(
        self,
        binary: str,
        *,
        keeper: IServiceKeeper,
        hold: VersionHold,
        corpus: ICompatibilityCorpus,
        versions: VersionProbe,
    ) -> None:
        self._binary = binary
        self._keeper = keeper
        self._hold = hold
        self._corpus = corpus
        self._versions = versions
        try:
            assert_admitted_range_has_corpus(corpus, OPENCODE_HARNESS_ID, ADMITTED_OPENCODE_V2_RANGE)
        except CorpusConfigurationError as exc:
            _log.warning("opencode 2 admitted-range corpus is misconfigured", detail=str(exc))

    def binary_present(self) -> bool:
        return self._versions.binary_present(self._binary)

    def probe_authentication(self) -> bool:
        """Whether the binary still answers ``--version`` in this environment; the service holds the operator's own
        credentials, which nothing here reads."""
        return self._versions.observe_version(self._binary) is not None

    def config_conflicts(self) -> tuple[str, ...]:
        """A service seen outside OpenCode 2's range since the runner started, and a binary and an answering service
        of different major versions: the runner can drive neither."""
        identity = self._keeper.answering()
        service = None if identity is None else normalize_opencode_v2_version(identity.version) or identity.version
        self._hold.observe(service)
        held = self._held()
        binary = normalize_opencode_v2_version(self._versions.observe_version(self._binary))
        if service is None or binary is None or _major(binary) == _major(service):
            return held
        return (*held, f"{self._binary} is version {binary}, but the OpenCode service it manages is version {service}")

    def _held(self) -> tuple[str, ...]:
        foreign = self._hold.foreign
        if foreign is None:
            return ()
        return (f"the OpenCode service reported version {foreign} since the runner started; restart the runner",)

    def supported_version(self) -> SpecifierSet:
        return ADMITTED_OPENCODE_V2_RANGE

    def supported_version_display(self) -> str:
        return ADMITTED_OPENCODE_V2_RANGE_TEXT

    def normalize_version(self, raw: str | None) -> str | None:
        return normalize_opencode_v2_version(raw)

    def classifies_offline(self) -> bool:
        return True

    def declared_degradations(self) -> tuple[DeclaredDegradation, ...]:
        """None: OpenCode 2's corpora declare their degradations by proof row, which this seam's probe vocabulary
        does not name."""
        return ()


def _major(version: str) -> int | None:
    try:
        return Version(version).major
    except InvalidVersion:
        return None


_harness_health_probe_conforms: type[IHarnessHealthProbe] = OpenCodeTwoHealthProbe

__all__ = ["OpenCodeTwoHealthProbe"]
