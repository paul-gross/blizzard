"""Read-side settings store double for snapshot-focused tests."""

from collections.abc import Mapping

from blizzard.runner.settings.ports import SettingsChange, SettingsSnapshot


class SnapshotSettingsStore:
    def snapshot(self) -> SettingsSnapshot:
        raise NotImplementedError

    def snapshot_with_provenance(self) -> tuple[SettingsSnapshot, Mapping[str, SettingsChange]]:
        return self.snapshot(), {}

    def changes(self, *, limit: int, before: int | None = None) -> tuple[SettingsChange, ...]:
        return ()

    def provenance(self) -> Mapping[str, SettingsChange]:
        return {}
