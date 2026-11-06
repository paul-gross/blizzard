"""The OpenCode health-probe binding."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from packaging.specifiers import SpecifierSet

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.compatibility import CompatibilityProbe
from blizzard.runner.harness.health import DeclaredDegradation
from blizzard.runner.harness.internal.version_probe import VersionProbe
from blizzard.runner.harness.offline_compatibility import (
    CorpusConfigurationError,
    ICompatibilityCorpus,
    admitted_corpus_versions,
    assert_admitted_range_has_corpus,
)
from blizzard.runner.harness.opencode.compatibility.probe import (
    ADMITTED_OPENCODE_RANGE,
    ADMITTED_OPENCODE_RANGE_DISPLAY,
)
from blizzard.runner.harness.opencode.version import normalize_opencode_version

_log = get_logger("blizzard.runner.harness.opencode")

_HARNESS_ID = "opencode"
#: The ``kv`` key OpenCode 2 writes into a 1.x store it has migrated in place.
MIGRATION_KV_KEY = "migration.v1-v2"


def store_migrated(store: Path) -> bool:
    """Whether OpenCode 2 has migrated the store at ``store``, read without writing or locking it; a store that is
    absent or unreadable reads as not migrated."""
    if not store.is_file():
        return False
    try:
        with closing(sqlite3.connect(f"{store.resolve().as_uri()}?mode=ro", uri=True, timeout=1.0)) as db:
            row = db.execute("select 1 from sqlite_master where type = 'table' and name = 'kv'").fetchone()
            return (
                row is not None
                and db.execute("select 1 from kv where key = ?", (MIGRATION_KV_KEY,)).fetchone() is not None
            )
    except sqlite3.Error:
        return False


def _degradations_from_manifest(corpus: ICompatibilityCorpus, version: str) -> tuple[DeclaredDegradation, ...]:
    """This ``version``'s own declared degradations, read from its committed corpus manifest —
    never a hardcoded Python literal describing only one version. A
    missing or malformed manifest reads as "no declared degradations"."""
    manifest = corpus.manifest(_HARNESS_ID, version)
    if manifest is None:
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


class OpenCodeHealthProbe:
    """The OpenCode binding's :class:`~blizzard.runner.harness.adapter.IHarnessHealthProbe`.
    Dumb, like the adapter it stands beside: reports evidence, never decides availability."""

    def __init__(
        self,
        binary: str = "opencode",
        *,
        auth_path: Path | None,
        corpus: ICompatibilityCorpus,
        versions: VersionProbe,
        store_path: Path | None = None,
    ) -> None:
        self._binary = binary
        self._store_path = store_path
        self._versions = versions
        # `None` reads as no credential.
        self._auth_path = auth_path
        self._corpus = corpus
        # The admitted range owes at least one committed corpus manifest inside it —
        # checked here, not at import, so a misconfigured corpus only degrades this binding.
        try:
            assert_admitted_range_has_corpus(corpus, _HARNESS_ID, ADMITTED_OPENCODE_RANGE)
        except CorpusConfigurationError as exc:
            _log.warning("opencode admitted-range corpus is misconfigured", detail=str(exc))

    def binary_present(self) -> bool:
        return self._versions.binary_present(self._binary)

    def probe_authentication(self) -> bool:
        """OpenCode exposes no provider-authentication subcommand, so this combines two free,
        local signals: the binary must still answer ``--version`` (it launches in this
        environment, not merely resolves on ``PATH``), and its credential-discovery path
        must hold a non-empty document. Neither reaches a provider, so neither proves a
        held credential is still valid."""
        if self._versions.observe_version(self._binary) is None:
            return False
        if self._auth_path is None:
            return False
        try:
            return self._auth_path.stat().st_size > 0
        except OSError:
            return False

    def config_conflicts(self) -> tuple[str, ...]:
        """A store OpenCode 2 has migrated beneath this runner: nothing OpenCode 1 writes to it afterward reaches
        OpenCode 2, so the binding refuses rather than let the two views diverge."""
        if self._store_path is None or not store_migrated(self._store_path):
            return ()
        return (
            f"the OpenCode store {self._store_path} was migrated to OpenCode 2 (kv {MIGRATION_KV_KEY});"
            " upgrade the runner's OpenCode to 2 and restart it",
        )

    def supported_version(self) -> SpecifierSet:
        return ADMITTED_OPENCODE_RANGE

    def supported_version_display(self) -> str:
        return ADMITTED_OPENCODE_RANGE_DISPLAY

    def normalize_version(self, raw: str | None) -> str | None:
        return normalize_opencode_version(raw)

    def classifies_offline(self) -> bool:
        return True

    def declared_degradations(self) -> tuple[DeclaredDegradation, ...]:
        """The union of every committed corpus inside the admitted range's own declared
        degradations, read from each such version's manifest — never a
        hardcoded tuple describing only one of them, and never one version's list picked
        arbitrarily, since this seam reports independent of any one observed version."""
        seen: dict[CompatibilityProbe, DeclaredDegradation] = {}
        for version in admitted_corpus_versions(self._corpus, _HARNESS_ID, ADMITTED_OPENCODE_RANGE):
            for degradation in _degradations_from_manifest(self._corpus, version):
                seen.setdefault(degradation.probe, degradation)
        return tuple(seen.values())


_harness_health_probe_conforms: type[IHarnessHealthProbe] = OpenCodeHealthProbe


__all__ = ["MIGRATION_KV_KEY", "OpenCodeHealthProbe", "store_migrated"]
