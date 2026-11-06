"""The OpenCode 2 health-probe binding. Beyond the binary and its admitted range, every capability check runs against
the operator's managed service, in order, and only the first that fails is reported, by name: a service version seen
outside the range since the runner started, a disabled service, a service that does not answer once started, a binary
and service of different major versions, a configured tier no authenticated provider lists, and a configured variant
its model does not offer. Each is reported as a configuration conflict, so the runner's log names it."""

from __future__ import annotations

import re
from collections.abc import Sequence

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
from blizzard.runner.harness.opencode_v2.service import (
    DISABLED_DETAIL,
    IServiceKeeper,
    ServiceDisabledError,
    ServiceUnavailableError,
)
from blizzard.runner.harness.opencode_v2.shapes import ListedModel
from blizzard.runner.harness.opencode_v2.version import (
    ADMITTED_OPENCODE_V2_RANGE,
    ADMITTED_OPENCODE_V2_RANGE_TEXT,
    normalize_opencode_v2_version,
)
from blizzard.runner.harness.opencode_v2.version_hold import VersionHold

_log = get_logger("blizzard.runner.harness.opencode")
_MODEL = re.compile(r"(?P<provider>[^/\s]+)/(?P<model>\S+)")


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
        model_aliases: Sequence[tuple[str, str]] = (),
        effort_aliases: Sequence[tuple[str, str]] = (),
    ) -> None:
        self._binary = binary
        self._model_aliases = tuple(model_aliases)
        self._variants = tuple(dict.fromkeys(variant for _, variant in effort_aliases if variant))
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
        """The first capability check that fails, by name; starts the service when none answers and the operator has
        not disabled it, and refuses a disabled one even while it runs."""
        failure = self._first_failure()
        return () if failure is None else (failure,)

    def _first_failure(self) -> str | None:
        held = self._held()
        if held is not None:
            return held
        # Checked even while a service runs: a disabled one is the operator's word that fleet sessions stay off it.
        if self._keeper.disabled():
            return DISABLED_DETAIL
        try:
            identity = self._keeper.ensure()
        except ServiceDisabledError as exc:
            return str(exc)
        except ServiceUnavailableError as exc:
            return f"no OpenCode service answers: {exc}"
        service = normalize_opencode_v2_version(identity.version) or identity.version
        self._hold.observe(service)
        held = self._held()
        if held is not None:
            return held
        binary = normalize_opencode_v2_version(self._versions.observe_version(self._binary))
        if binary is not None and _major(binary) != _major(service):
            return f"{self._binary} is version {binary}, but the OpenCode service it manages is version {service}"
        return self._model_failure()

    def _model_failure(self) -> str | None:
        """A configured tier whose model no provider the service is authenticated to lists, or a configured variant
        a tier's model does not offer — read from the model listing alone, never the service's credentials."""
        tiers = [(tier, match) for tier, model in self._model_aliases if (match := _MODEL.fullmatch(model)) is not None]
        if not tiers:
            return None
        try:
            listed = {(model.provider_id, model.id): model for model in self._keeper.listed_models()}
        except ServiceUnavailableError as exc:
            return str(exc)
        for tier, match in tiers:
            model: ListedModel | None = listed.get((match["provider"], match["model"]))
            reference = f"{match['provider']}/{match['model']}"
            if model is None:
                return (
                    f"tier {tier} names {reference}, which no provider the OpenCode service is authenticated to lists"
                )
            missing = next((variant for variant in self._variants if variant not in model.variants), None)
            if missing is not None:
                return f"tier {tier}'s model {reference} offers no variant {missing}"
        return None

    def _held(self) -> str | None:
        foreign = self._hold.foreign
        if foreign is None:
            return None
        return f"the OpenCode service reported version {foreign} since the runner started; restart the runner"

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
