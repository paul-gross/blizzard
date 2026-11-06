"""The OpenCode 2 health probe, and the capability it leaves: a 2.x service admitted, another major version refused
until restart, and every service-side check run in order, the first that fails reported by name — the service started
when none answers, a disabled service refused, the configured tiers resolved through the model listing alone, and
every configured variant offered by its model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.health import HarnessHealthCause
from blizzard.runner.harness.health_cache import HarnessHealthCache
from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.internal.version_probe import VersionProbe
from blizzard.runner.harness.opencode_v2.driver import ServiceIdentity
from blizzard.runner.harness.opencode_v2.health import OpenCodeTwoHealthProbe
from blizzard.runner.harness.opencode_v2.service import ServiceDisabledError, ServiceUnavailableError
from blizzard.runner.harness.opencode_v2.shapes import ListedModel
from blizzard.runner.harness.opencode_v2.version_hold import VersionHold
from tests.support.child_launch import CHILDREN, tool_env

_LUNA = ListedModel("openai", "gpt-6-luna", ("low", "medium", "high"))
_TIERS = (("tier:deep", "openai/gpt-6-luna"),)


@dataclass
class _Service:
    """A managed service whose version, start, setting, and model listing a test sets."""

    version: str | None = "2.1.4"
    starts: bool = True
    is_disabled: bool = False
    models: tuple[ListedModel, ...] | None = (_LUNA,)
    started: list[str] = field(default_factory=list[str])

    def answering(self) -> ServiceIdentity | None:
        return None if self.version is None else ServiceIdentity("svc", 1, self.version)

    def ensure(self) -> ServiceIdentity:
        if self.version is None:
            if self.is_disabled:
                raise ServiceDisabledError("the operator has disabled the managed OpenCode service")
            if not self.starts:
                raise ServiceUnavailableError("no OpenCode service answered within 30s of `service start`")
            self.started.append("start")
            self.version = "2.1.4"
        return ServiceIdentity("svc", 1, self.version)

    def disabled(self) -> bool:
        return self.is_disabled

    def listed_models(self) -> tuple[ListedModel, ...]:
        if self.models is None:
            raise ServiceUnavailableError("cannot read the OpenCode service's model listing: 401")
        return self.models


class _Tiers:
    def resolve_model_strict(self, preferences: object) -> str | None:
        return "openai/gpt-6-luna"


def _probe(
    tmp_path: Path,
    service: _Service,
    *,
    binary_version: str = "2.1.4",
    tiers: tuple[tuple[str, str], ...] = _TIERS,
    efforts: tuple[tuple[str, str], ...] = (),
) -> OpenCodeTwoHealthProbe:
    binary = tmp_path / "opencode"
    binary.write_text(f"#!/bin/sh\necho '{binary_version}'\n")
    binary.chmod(0o755)
    return OpenCodeTwoHealthProbe(
        str(binary),
        keeper=service,
        hold=VersionHold(),
        corpus=CommittedCorpus(),
        versions=VersionProbe(CHILDREN, tool_env()),
        model_aliases=tiers,
        effort_aliases=efforts,
    )


@pytest.mark.parametrize(
    ("binary", "service", "conflicts"),
    [("2.1.4", "2.1.4", False), ("2.2.0", "2.1.4", False), ("3.0.0", "2.1.4", True)],
)
def test_a_binary_and_service_of_different_major_versions_conflict(
    tmp_path: Path, binary: str, service: str, conflicts: bool
) -> None:
    probe = _probe(tmp_path, _Service(version=service), binary_version=binary)
    assert bool(probe.config_conflicts()) is conflicts
    assert probe.binary_present() and probe.probe_authentication()


@pytest.mark.parametrize(
    ("observed", "available", "cause"),
    [
        ("2.1.4", True, None),
        ("3.0.0", False, HarnessHealthCause.INCOMPATIBLE_VERSION),
        ("1.18.33", False, HarnessHealthCause.INCOMPATIBLE_VERSION),
    ],
)
def test_the_service_version_is_admitted_by_the_opencode_2_range_alone(
    tmp_path: Path, observed: str, available: bool, cause: HarnessHealthCause | None
) -> None:
    probe = _probe(tmp_path, _Service())
    cache = HarnessHealthCache(
        clock=FixedClock(datetime(2026, 11, 5, tzinfo=UTC)),
        probes={"opencode": probe},
        corpus=CommittedCorpus(),
        selftest_results=None,
    )
    result = cache.refresh("opencode", adapter=_Tiers(), observed_version=observed)
    assert result is not None and result.available is available and result.cause is cause


def test_a_service_seen_on_another_major_version_stays_a_conflict_after_it_returns(tmp_path: Path) -> None:
    service = _Service()
    probe = _probe(tmp_path, service)
    assert probe.config_conflicts() == ()
    service.version = "3.0.0"
    assert probe.config_conflicts()
    service.version = "2.1.4"
    (conflict,) = probe.config_conflicts()
    assert "3.0.0" in conflict and "restart the runner" in conflict


def test_a_probe_starts_the_service_when_none_answers(tmp_path: Path) -> None:
    service = _Service(version=None)
    assert _probe(tmp_path, service).config_conflicts() == ()
    assert service.started == ["start"]


@pytest.mark.parametrize(
    ("service", "efforts", "named"),
    [
        (_Service(version=None, is_disabled=True), (), "disabled"),
        (_Service(version=None, starts=False), (), "no OpenCode service answers"),
        (_Service(models=None), (), "model listing"),
        (_Service(models=(ListedModel("openai", "gpt-6-sol", ("high",)),)), (), "openai/gpt-6-luna"),
        (_Service(), (("max", "xhigh"),), "offers no variant xhigh"),
    ],
    ids=["disabled", "never-answers", "listing-refused", "tier-unlisted", "variant-missing"],
)
def test_the_first_failing_check_is_named(
    tmp_path: Path, service: _Service, efforts: tuple[tuple[str, str], ...], named: str
) -> None:
    (conflict,) = _probe(tmp_path, service, efforts=efforts).config_conflicts()
    assert named in conflict


def test_a_disabled_service_is_refused_even_while_it_runs(tmp_path: Path) -> None:
    (conflict,) = _probe(tmp_path, _Service(is_disabled=True)).config_conflicts()
    assert "disabled" in conflict


def test_checks_run_in_order_and_stop_at_the_first_that_fails(tmp_path: Path) -> None:
    """A disabled service is named, not the model listing it cannot answer; a major mismatch, not a missing variant."""
    disabled = _Service(version=None, is_disabled=True, models=None)
    (conflict,) = _probe(tmp_path, disabled).config_conflicts()
    assert "disabled" in conflict
    mismatched = _Service(models=(ListedModel("openai", "gpt-6-luna", ()),))
    (conflict,) = _probe(tmp_path, mismatched, binary_version="3.0.0", efforts=(("high", "high"),)).config_conflicts()
    assert "3.0.0" in conflict and "variant" not in conflict


def test_every_configured_variant_is_checked_against_every_configured_tier(tmp_path: Path) -> None:
    efforts = (("low", "low"), ("high", "high"))
    tiers = (("tier:deep", "openai/gpt-6-luna"), ("tier:fast", "opencode/crouton-alpha-free"))
    service = _Service(models=(_LUNA, ListedModel("opencode", "crouton-alpha-free", ("high",))))
    (conflict,) = _probe(tmp_path, service, tiers=tiers, efforts=efforts).config_conflicts()
    assert conflict == "tier tier:fast's model opencode/crouton-alpha-free offers no variant low"


def test_no_configured_tier_reads_no_listing(tmp_path: Path) -> None:
    """With no tier mapped, OpenCode's own configured default model is in force, and nothing is checked against it."""
    assert _probe(tmp_path, _Service(models=None), tiers=()).config_conflicts() == ()


def test_a_failing_check_leaves_the_capability_unavailable_as_a_named_conflict(tmp_path: Path) -> None:
    cache = HarnessHealthCache(
        clock=FixedClock(datetime(2026, 11, 5, tzinfo=UTC)),
        probes={"opencode": _probe(tmp_path, _Service(version=None, is_disabled=True))},
        corpus=CommittedCorpus(),
        selftest_results=None,
    )
    result = cache.refresh("opencode", adapter=_Tiers(), observed_version="2.1.4")
    assert result is not None and not result.available and result.cause is HarnessHealthCause.CONFIG_CONFLICT
