"""The runner's harness-health cache and the read-only seam its consumers depend on.

Composition-root-owned and as long-lived as the runner process: the loop refreshes it, the
local API and :class:`~blizzard.runner.lifecycle.session.HarnessSelector` only read it through
:class:`IReadHarnessHealth`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.admission import admission_verdict
from blizzard.runner.harness.health import HarnessHealthEvidence, HarnessHealthResult, evaluate_harness_health
from blizzard.runner.harness.offline_compatibility import classify_offline
from blizzard.runner.harness.selftest_result import IReadSelfTestResultRepository, selftest_failed

_log = get_logger("blizzard.runner.harness.health_cache")


class _ResolvesModelStrict(Protocol):
    """The one sliver of :class:`~blizzard.runner.harness.adapter.IHarnessModelResolution`
    the unmapped-tier check needs (``bzh:seam-size-ceiling``) — a full adapter's own
    ``resolve_model_strict`` already satisfies this."""

    def resolve_model_strict(self, preferences: Sequence[str]) -> str | None: ...


#: How long a version is trusted before re-probing — long enough an idle runner isn't shelling out every tick.
HARNESS_VERSION_REFRESH_SECONDS = 600.0

#: Distinguishes "never computed" from a genuinely ``None``-valued previous observation.
_UNSET = object()


class IReadHarnessHealth(Protocol):
    """The read side of :class:`HarnessHealthCache` — what the local API and the harness
    selector need (``bzh:seam-size-ceiling``); neither may trigger a probe."""

    def get(self, harness_id: str) -> HarnessHealthResult | None: ...

    def displayed_version(self, harness_id: str) -> str | None: ...

    def admitted_range(self, harness_id: str) -> str | None: ...


def _unmapped_tiers(adapter: _ResolvesModelStrict, declared: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    """Every tier the operator configured this harness to resolve (``declared``, the raw
    alias table off ``RunnerConfig``) that the adapter itself cannot actually resolve —
    e.g. a declared alias mapped to an empty native model. Empty for a harness declaring
    no aliases at all, which asserts no configured tier to fail this check."""
    return tuple(tier for tier, _ in declared if adapter.resolve_model_strict((tier,)) is None)


@dataclass
class HarnessHealthCache:
    """Every configured harness binding's last-computed health result, held
    across ticks like :class:`HarnessVersionCache` — health evidence includes subprocess
    and credential probes, so recomputing on every peek would put that cost on the read
    path instead of this cache's own bounded refresh window."""

    clock: IClock
    probes: Mapping[str, IHarnessHealthProbe]
    #: ``None`` on a store-free composition (the OpenAPI exporter, a unit test) — reads as never-run.
    selftest_results: IReadSelfTestResultRepository | None
    #: Per-harness declared (tier, native-model) pairs this runner resolves through it specifically.
    configured_tiers: Mapping[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)
    refresh_seconds: float = HARNESS_VERSION_REFRESH_SECONDS
    _results: dict[str, HarnessHealthResult] = field(default_factory=dict, compare=False)
    _computed_at: dict[str, datetime] = field(default_factory=dict, compare=False)
    _last_version: dict[str, object] = field(default_factory=dict, compare=False)
    _last_selftest: dict[str, object] = field(default_factory=dict, compare=False)

    def refresh(
        self, harness_id: str, *, adapter: _ResolvesModelStrict, observed_version: str | None
    ) -> HarnessHealthResult | None:
        """Recompute (or reuse) ``harness_id``'s health; ``None`` for a harness this
        composition wired no health probe for. Recomputes when the refresh window elapses,
        the observed version changes, or a new selftest result lands — never merely because
        a peek asked; the first call for a harness always computes, since an empty cache is
        always stale (covering "at daemon start" with no separate startup hook)."""
        probe = self.probes.get(harness_id)
        if probe is None:
            return None
        latest = self.selftest_results.latest_selftest_result(harness_id) if self.selftest_results is not None else None
        selftest_marker = (latest.status, latest.recorded_at) if latest is not None else None
        now = self.clock.now()
        computed_at = self._computed_at.get(harness_id)
        stale = computed_at is None or (now - computed_at).total_seconds() >= self.refresh_seconds
        changed = (
            self._last_version.get(harness_id, _UNSET) != observed_version
            or self._last_selftest.get(harness_id, _UNSET) != selftest_marker
        )
        if not stale and not changed and harness_id in self._results:
            return self._results[harness_id]
        supported_version = probe.supported_version()
        normalized_version = probe.normalize_version(observed_version)
        classifies_offline = probe.classifies_offline()
        conflicts = probe.config_conflicts()
        result = evaluate_harness_health(
            HarnessHealthEvidence(
                harness_id=harness_id,
                binary_present=probe.binary_present(),
                version_declared=supported_version is not None,
                version_admitted=admission_verdict(normalized_version, supported_version),
                version_classification=(
                    classify_offline(harness_id, normalized_version, supported_version)
                    if supported_version is not None and classifies_offline
                    else None
                ),
                authenticated=probe.probe_authentication(),
                unmapped_tiers=_unmapped_tiers(adapter, self.configured_tiers.get(harness_id, ())),
                selftest_failed=selftest_failed(latest),
                corpus_backed=classifies_offline,
                degradations=probe.declared_degradations(),
                config_conflicts=conflicts,
            )
        )
        if conflicts:
            _log.warning("harness config conflict", harness_id=harness_id, conflicts=list(conflicts))
        self._results[harness_id] = result
        self._computed_at[harness_id] = now
        self._last_version[harness_id] = observed_version
        self._last_selftest[harness_id] = selftest_marker
        return result

    def get(self, harness_id: str) -> HarnessHealthResult | None:
        """The last-computed result, or ``None`` when :meth:`refresh` has never run for
        this harness — :class:`~blizzard.runner.lifecycle.session.HarnessSelector`'s own read,
        which must never itself trigger a probe mid-selection."""
        return self._results.get(harness_id)

    def observed_version(self, harness_id: str) -> str | None:
        """The version last given to :meth:`refresh` for ``harness_id`` — ``None`` both
        when refresh has never run and when the observed version genuinely was ``None``,
        the same ambiguity :meth:`get` already carries for "no result yet"."""
        value = self._last_version.get(harness_id)
        return value if isinstance(value, str) else None

    def admitted_range(self, harness_id: str) -> str | None:
        """``harness_id``'s own declared admitted-version range as a display string, or
        ``None`` when this cache holds no probe for it or that probe declares no range
        at all — a probe's own static declaration, not a live read, so callers may reach
        it without themselves depending on :attr:`probes`."""
        probe = self.probes.get(harness_id)
        return probe.supported_version_display() if probe is not None else None

    def displayed_version(self, harness_id: str) -> str | None:
        """:meth:`observed_version`, normalized through this binding's own probe when its raw
        shape allows it — so a diagnostics display never shows a version alongside
        :meth:`admitted_range` in a form that looks non-member when it actually is. Falls
        back to the raw form when this cache holds no probe for ``harness_id`` or that
        probe's own shape doesn't normalize."""
        raw = self.observed_version(harness_id)
        probe = self.probes.get(harness_id)
        normalized = probe.normalize_version(raw) if probe is not None else None
        return normalized or raw
