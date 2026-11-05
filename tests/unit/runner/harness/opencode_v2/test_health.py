"""The OpenCode 2 health probe, and the capability it leaves: a 2.x service admitted, another major version refused
until restart, and a binary and service of different major versions a conflict."""

from __future__ import annotations

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
from tests.support.child_launch import CHILDREN, tool_env


class _Service:
    def __init__(self, version: str | None) -> None:
        self.version = version

    def answering(self) -> ServiceIdentity | None:
        return None if self.version is None else ServiceIdentity("svc", 1, self.version)

    def ensure(self) -> ServiceIdentity:
        raise AssertionError("health never starts a service")


class _Tiers:
    def resolve_model_strict(self, preferences: object) -> str | None:
        return "openai/gpt-6-luna"


def _probe(tmp_path: Path, binary_version: str, service_version: str | None) -> OpenCodeTwoHealthProbe:
    binary = tmp_path / "opencode"
    binary.write_text(f"#!/bin/sh\necho '{binary_version}'\n")
    binary.chmod(0o755)
    return OpenCodeTwoHealthProbe(
        str(binary),
        keeper=_Service(service_version),
        corpus=CommittedCorpus(),
        versions=VersionProbe(CHILDREN, tool_env()),
    )


@pytest.mark.parametrize(
    ("binary", "service", "conflicts"),
    [("2.1.4", "2.1.4", False), ("2.1.4", None, False), ("2.2.0", "2.1.4", False), ("3.0.0", "2.1.4", True)],
)
def test_a_binary_and_service_of_different_major_versions_conflict(
    tmp_path: Path, binary: str, service: str | None, conflicts: bool
) -> None:
    probe = _probe(tmp_path, binary, service)
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
    probe = _probe(tmp_path, "2.1.4", "2.1.4")
    cache = HarnessHealthCache(
        clock=FixedClock(datetime(2026, 11, 5, tzinfo=UTC)),
        probes={"opencode": probe},
        corpus=CommittedCorpus(),
        selftest_results=None,
    )
    result = cache.refresh("opencode", adapter=_Tiers(), observed_version=observed)
    assert result is not None and result.available is available and result.cause is cause
