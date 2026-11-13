from datetime import UTC, datetime, timedelta
from pathlib import Path

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.binary_identity import BinaryIdentity
from blizzard.runner.harness.binding_selection import CachedVersionProbe
from blizzard.runner.harness.capability_snapshot import BinaryVersionCache, HarnessVersionCache


class Identities:
    def __init__(self, identity: BinaryIdentity | None) -> None:
        self.identity = identity

    def identify(self, binary: str) -> BinaryIdentity | None:
        return self.identity


def test_success_is_shared_by_identity_until_changed_or_invalidated() -> None:
    identities = Identities(BinaryIdentity(Path("/bin/tool"), 1, 10))
    cache = BinaryVersionCache(identities)
    calls: list[str] = []

    def observe() -> str:
        calls.append("probe")
        return str(len(calls))

    assert cache.get("tool", observe) == "1"
    assert cache.get("alias", observe) == "1"
    identities.identity = BinaryIdentity(Path("/bin/tool"), 1, 11)
    assert cache.get("tool", observe) == "2"
    identities.identity = BinaryIdentity(Path("/other/tool"), 1, 11)
    assert cache.get("tool", observe) == "3"
    cache.forget("tool")
    assert cache.get("tool", observe) == "4"


def test_missing_identity_and_failed_observations_are_not_reused() -> None:
    original = BinaryIdentity(Path("/bin/tool"), 1, 10)
    identities = Identities(original)
    cache = BinaryVersionCache(identities)
    assert cache.get("tool", lambda: "old") == "old"
    identities.identity = None
    assert cache.get("tool", lambda: None) is None
    identities.identity = original
    assert cache.get("tool", lambda: None) is None
    assert cache.get("tool", lambda: "") == ""
    assert cache.get("tool", lambda: "new") == "new"


def test_replacement_during_probe_does_not_cache_the_observation() -> None:
    identities = Identities(BinaryIdentity(Path("/bin/tool"), 1, 10))
    cache = BinaryVersionCache(identities)

    def replace_during_probe() -> str:
        identities.identity = BinaryIdentity(Path("/bin/tool"), 1, 11)
        return "old"

    assert cache.get("tool", replace_during_probe) == "old"
    assert cache.get("tool", lambda: "new") == "new"
    assert cache.get("tool", lambda: "not called") == "new"


def test_selection_and_health_probes_share_binary_evidence() -> None:
    identities = Identities(BinaryIdentity(Path("/bin/tool"), 1, 10))
    cache = BinaryVersionCache(identities)

    class Probe:
        calls = 0

        def binary_present(self, binary: str) -> bool:
            return True

        def observe_version(self, binary: str) -> str:
            self.calls += 1
            return "2.0"

    raw = Probe()
    selection = CachedVersionProbe(raw, cache)
    health = CachedVersionProbe(raw, cache)
    assert selection.binary_present("tool")
    assert selection.observe_version("tool") == "2.0"
    assert health.observe_version("tool") == "2.0"
    assert raw.calls == 1


def test_service_observation_refreshes_independently_and_invalidates_binary() -> None:
    identities = Identities(BinaryIdentity(Path("/bin/tool"), 1, 10))
    binary = BinaryVersionCache(identities)
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    versions = HarnessVersionCache(
        clock, binary_versions=binary, binaries={"service": "tool"}, service_harnesses=frozenset({"service"})
    )
    observations: list[int] = []

    def service() -> str:
        observations.append(1)
        return str(len(observations))

    assert binary.get("tool", lambda: "binary") == "binary"
    assert versions.get("service", service) == "1"
    assert versions.get("service", service) == "1"
    clock.advance(timedelta(seconds=600))
    assert versions.get("service", service) == "2"
    assert binary.get("tool", lambda: "wrong") == "binary"
    identities.identity = BinaryIdentity(Path("/bin/tool"), 2, 10)
    assert versions.get("service", service) == "3"
    versions.forget("service")
    assert versions.get("service", service) == "4"
    assert binary.get("tool", lambda: "new") == "new"
