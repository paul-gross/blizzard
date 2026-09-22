"""The OpenCode health-probe binding (blizzard#438).

Standalone, like :class:`~blizzard.runner.harness.internal.opencode_probe.
OpenCodeCompatibilityProbe`: the health-probe seam is a narrow, separately-injected
collaborator rather than a slice folded onto :class:`~blizzard.runner.harness.internal.
opencode_adapter.OpenCodeAdapter` itself."""

from __future__ import annotations

import json
import os
from pathlib import Path

from packaging.specifiers import SpecifierSet

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.compatibility import CompatibilityProbe
from blizzard.runner.harness.health import DeclaredDegradation
from blizzard.runner.harness.internal import harness_shared
from blizzard.runner.harness.internal.offline_compatibility import (
    DEFAULT_CORPUS_ROOT,
    CorpusConfigurationError,
    admitted_corpus_versions,
    assert_admitted_range_has_corpus,
)
from blizzard.runner.harness.internal.opencode_probe import (
    ADMITTED_OPENCODE_RANGE,
    ADMITTED_OPENCODE_RANGE_DISPLAY,
)

_log = get_logger("blizzard.runner.harness.opencode")

_HARNESS_ID = "opencode"


def _degradations_from_manifest(version: str, *, corpus_root: Path) -> tuple[DeclaredDegradation, ...]:
    """This ``version``'s own declared degradations, read from its committed corpus manifest
    (blizzard#438) — never a hardcoded Python literal describing only one version. A
    missing or malformed manifest reads as "no declared degradations", the same fail-soft
    posture :func:`~blizzard.runner.harness.internal.offline_compatibility.classify_offline`
    already takes for a manifest it cannot read."""
    manifest_path = corpus_root / _HARNESS_ID / version / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        return ()
    if not isinstance(manifest, dict):
        return ()
    declared = manifest.get("declared_degradations")
    if not isinstance(declared, list):
        return ()
    degradations: list[DeclaredDegradation] = []
    for entry in declared:
        if not isinstance(entry, dict):
            continue
        probe_value = entry.get("probe")
        summary = entry.get("summary")
        if not isinstance(probe_value, str) or not isinstance(summary, str) or not summary.strip():
            continue
        try:
            probe = CompatibilityProbe(probe_value)
        except ValueError:
            continue
        degradations.append(DeclaredDegradation(probe=probe, summary=summary))
    return tuple(degradations)


def _default_opencode_auth_path() -> Path:
    """Where OpenCode's own credential discovery reads from — mirrors
    ``opencode_scratch_config.py``'s ``auth_path`` (``<data-home>/opencode/auth.json``),
    the same path the compatibility proof's disposable-auth provisioning copies from."""
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data_home) / "opencode" / "auth.json"


class OpenCodeHealthProbe:
    """The OpenCode binding's :class:`~blizzard.runner.harness.adapter.IHarnessHealthProbe`.
    Dumb, like the adapter it stands beside: reports evidence, never decides availability."""

    def __init__(
        self, binary: str = "opencode", *, auth_path: str | None = None, corpus_root: Path = DEFAULT_CORPUS_ROOT
    ) -> None:
        self._binary = binary
        # Injectable for testability (`bzh:dependency-injection`); defaults to OpenCode's
        # own real credential-discovery path.
        self._auth_path = Path(auth_path) if auth_path is not None else _default_opencode_auth_path()
        self._corpus_root = corpus_root
        # The admitted range owes at least one committed corpus manifest inside it (D1) —
        # checked here, not at import, so a misconfigured corpus only degrades this binding.
        try:
            assert_admitted_range_has_corpus(_HARNESS_ID, ADMITTED_OPENCODE_RANGE, corpus_root=corpus_root)
        except CorpusConfigurationError as exc:
            _log.warning("opencode admitted-range corpus is misconfigured", detail=str(exc))

    def binary_present(self) -> bool:
        return harness_shared.binary_present(self._binary)

    def probe_authentication(self) -> bool:
        """OpenCode exposes no provider-authentication subcommand, so this combines two free,
        local signals: the binary must still answer ``--version`` (it launches in this
        environment, not merely resolves on ``PATH``), and its credential-discovery path
        must hold a non-empty document. Neither reaches a provider, so neither proves a
        held credential is still valid."""
        if harness_shared.observe_version(self._binary) is None:
            return False
        try:
            return self._auth_path.stat().st_size > 0
        except OSError:
            return False

    def supported_version(self) -> SpecifierSet:
        return ADMITTED_OPENCODE_RANGE

    def supported_version_display(self) -> str:
        return ADMITTED_OPENCODE_RANGE_DISPLAY

    def normalize_version(self, raw: str | None) -> str | None:
        return harness_shared.normalize_opencode_version(raw)

    def classifies_offline(self) -> bool:
        return True

    def declared_degradations(self) -> tuple[DeclaredDegradation, ...]:
        """The union of every committed corpus inside the admitted range's own declared
        degradations (blizzard#438), read from each such version's manifest — never a
        hardcoded tuple describing only one of them, and never one version's list picked
        arbitrarily, since this seam reports independent of any one observed version."""
        seen: dict[CompatibilityProbe, DeclaredDegradation] = {}
        for version in admitted_corpus_versions(_HARNESS_ID, ADMITTED_OPENCODE_RANGE, corpus_root=self._corpus_root):
            for degradation in _degradations_from_manifest(version, corpus_root=self._corpus_root):
                seen.setdefault(degradation.probe, degradation)
        return tuple(seen.values())


def _conforms_harness_health_probe(x: OpenCodeHealthProbe) -> IHarnessHealthProbe:
    return x


__all__ = ["OpenCodeHealthProbe"]
