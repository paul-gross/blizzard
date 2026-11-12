"""Hub settings authoring seam."""

from __future__ import annotations

from collections.abc import Callable

# Protects only the in-process listener set, not a store decision.
# ast-grep-ignore: bzh:store-exclusive-write
from threading import Lock

from blizzard.foundation.settings_record.decision import Validate, decide
from blizzard.foundation.settings_record.vocabulary import Change, Door, Entry, FieldChange
from blizzard.hub.settings.model import HubSettingsApply, IHubSettingsSnapshots, IWriteHubSettings
from blizzard.hub.settings.values import thaw


class HubSettingsAuthoring:
    def __init__(self, reader: IHubSettingsSnapshots, store: IWriteHubSettings, validate: Validate) -> None:
        self._reader = reader
        self._store = store
        self._validate = validate
        self._listeners: set[Callable[[frozenset[str]], None]] = set()
        self._listeners_lock = Lock()

    def subscribe(self, listener: Callable[[frozenset[str]], None]) -> Callable[[], None]:
        """Observe committed changes; the returned function removes the listener."""
        with self._listeners_lock:
            self._listeners.add(listener)

        def unsubscribe() -> None:
            with self._listeners_lock:
                self._listeners.discard(listener)

        return unsubscribe

    def submit(self, request: HubSettingsApply) -> tuple[FieldChange, ...]:
        snapshot = self._reader.snapshot()
        entries = {key: Entry(thaw(entry.value), entry.at_default) for key, entry in snapshot.entries.items()}
        decide(snapshot.revision, entries, request.change, if_match=request.if_match, validate=self._validate)
        changed = self._store.apply(
            request.change, if_match=request.if_match, door=request.door, actor=request.actor, apply_id=request.apply_id
        )
        self.committed(changed)
        return changed

    def committed(self, changed: tuple[FieldChange, ...]) -> None:
        """Notify listeners after a successful committed write (never a dry run)."""
        if changed:
            with self._listeners_lock:
                listeners = tuple(self._listeners)
            keys = frozenset(item.key for item in changed)
            for listener in listeners:
                listener(keys)

    def apply(
        self, change: Change, *, if_match: int | None, door: Door, actor: str, apply_id: str | None = None
    ) -> tuple[FieldChange, ...]:
        return self.submit(HubSettingsApply(change, if_match, door, actor, apply_id))
