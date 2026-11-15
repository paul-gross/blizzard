"""Rebuilding one live-config consumer leaves the other's state intact."""

from pathlib import Path

from blizzard.foundation.flags import FlagRegistry
from blizzard.foundation.settings_record.vocabulary import Change
from blizzard.hub.auth.throttle import LiveIpThrottle
from blizzard.hub.composition import build_hub_settings
from blizzard.hub.config_keys import HUB_REGISTRY
from blizzard.hub.egress.factory import ConfiguredWriter
from blizzard.hub.egress.internal.ndjson import NdjsonEgressWriter
from blizzard.hub.egress.writer import EgressWriterSettings
from blizzard.hub.settings.footing import HubSettingsFooting
from tests.support.scaffolding import build_hub, hub_level_connections


def test_throttle_and_writer_rebuild_independently(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    footing = HubSettingsFooting(tmp_path, None, None, False, FlagRegistry(), False, HUB_REGISTRY, {})
    settings = build_hub_settings(hub_level_connections(hub.engine), hub.clock, footing)
    throttle = LiveIpThrottle(clock=hub.clock)
    initial = footing.resolve(settings.reader.snapshot().entries).egress
    writer_settings = EgressWriterSettings(initial.max_rows_per_file, initial.min_free_bytes)

    def build(value: EgressWriterSettings) -> NdjsonEgressWriter:
        return NdjsonEgressWriter(tmp_path / "export", value, "token")

    writers = ConfiguredWriter(build(writer_settings), writer_settings, build)

    def current():
        snapshot = settings.reader.snapshot()
        return snapshot, footing.resolve(snapshot.entries)

    def allow() -> bool:
        def read():
            snapshot, config = current()
            values = config.auth.login_throttle
            return snapshot, (values.enabled, values.capacity, values.refill_seconds)

        with throttle.capture(read) as (_, generation):
            return throttle.allow("ip", generation)

    settings.authoring.apply(
        Change("edit", sets={"auth.login_throttle.capacity": 1}), if_match=0, door="api", actor="test"
    )
    first_writer = writers.for_config(current()[1].egress)
    assert allow()
    assert not allow()
    settings.authoring.apply(
        Change("edit", sets={"auth.login_throttle.capacity": 2}), if_match=1, door="api", actor="test"
    )
    assert allow()
    assert allow()
    assert not allow()
    assert writers.for_config(current()[1].egress) is first_writer
    settings.authoring.apply(
        Change("edit", sets={"egress.max_rows_per_file": 123}), if_match=2, door="api", actor="test"
    )
    second_writer = writers.for_config(current()[1].egress)
    assert second_writer is not first_writer
    assert not allow()
    settings.authoring.apply(
        Change("edit", sets={"transcripts.record_max_bytes": 42}), if_match=3, door="api", actor="test"
    )
    assert writers.for_config(current()[1].egress) is second_writer
    assert not allow()
