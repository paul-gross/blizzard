"""Evidence-driven checks for repeated, in-flight OpenCode session exports.

The compatibility proof cannot treat an export as a snapshot whose array offsets are a cursor.
This module compares stable message/part identities and their revisions across exports, including
the pending-to-complete transition and the history removal that follows compaction.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from blizzard.runner.harness.internal.opencode_cursor import MessagePartCursor, MessagePartIdentity, records_for_export
from blizzard.runner.harness.internal.opencode_shapes import OpenCodeSessionExport


@dataclass(frozen=True)
class TranscriptExportSample:
    """One parsed export, the phase that captured it, and whether its producer was still alive."""

    name: str
    live: bool
    export: OpenCodeSessionExport
    phase: Literal["turn", "compaction"] = "turn"


@dataclass(frozen=True)
class TranscriptProof:
    """The observable claims required before transcript compatibility is admitted."""

    during_export: bool
    after_export: bool
    stable_unique_identities: bool
    pending_to_completed: bool
    compaction_observed: bool
    appended_after_compaction: tuple[MessagePartIdentity, ...]
    retained_history_not_replayed: bool
    failures: tuple[str, ...]
    read_failures: tuple[str, ...] = ()
    cursor_failures: tuple[str, ...] = ()
    repeated_live_exports: bool = False
    repeated_after_exports: bool = False
    compaction_pruned: bool = False

    @property
    def valid(self) -> bool:
        return not self.failures

    def to_payload(self) -> dict[str, object]:
        return {
            "during_export": self.during_export,
            "after_export": self.after_export,
            "stable_unique_identities": self.stable_unique_identities,
            "pending_to_completed": self.pending_to_completed,
            "compaction_observed": self.compaction_observed,
            "appended_after_compaction": [_identity_payload(identity) for identity in self.appended_after_compaction],
            "retained_history_not_replayed": self.retained_history_not_replayed,
            "repeated_live_exports": self.repeated_live_exports,
            "repeated_after_exports": self.repeated_after_exports,
            "compaction_pruned": self.compaction_pruned,
            "failures": list(self.failures),
            "read_failures": list(self.read_failures),
            "cursor_failures": list(self.cursor_failures),
        }


def inspect_transcript(samples: Iterable[TranscriptExportSample]) -> TranscriptProof:
    """Check a sequence of live and post-exit exports without trusting array positions."""

    ordered = tuple(samples)
    # The two transcript probes are separate contracts: reads answer whether an export is
    # obtainable and coherent, the cursor answers whether identities admit exactly once.
    read_failures: list[str] = []
    cursor_failures: list[str] = []
    # Only the turn phase can witness a read taken while the turn itself was running; the
    # compaction phase captures a separately owned server process long after that turn exited.
    turn = tuple(sample for sample in ordered if sample.phase == "turn")
    during_export = any(sample.live for sample in turn)
    after_export = any(not sample.live for sample in turn)
    repeated_live_exports = sum(sample.live for sample in turn) >= 2
    repeated_after_exports = sum(not sample.live for sample in turn) >= 2
    if not during_export:
        read_failures.append("no export was captured while the turn was live")
    if not after_export:
        read_failures.append("no export was captured after the turn exited")
    if not repeated_live_exports:
        read_failures.append("fewer than two exports were captured while the turn was live")
    if not repeated_after_exports:
        read_failures.append("fewer than two exports were captured after the turn exited")

    stable_unique = _stable_unique_identities(ordered)
    if not stable_unique:
        cursor_failures.append("repeated exports did not preserve unique stable message and part identities")

    pending_to_completed = _has_pending_to_completed(ordered)
    if not pending_to_completed:
        cursor_failures.append("no tool identity transitioned from pending/running to completed")

    compaction_observed, compaction_pruned, appended, no_replay = _cursor_history(ordered)
    if not compaction_observed:
        cursor_failures.append("no compaction marker was observed after an earlier export")
    if not compaction_pruned:
        cursor_failures.append("compaction did not remove a previously seen identity")
    if not appended:
        cursor_failures.append("no genuinely appended identity was observed after compaction")
    if not no_replay:
        cursor_failures.append("an identity removed from a later export reappeared in one after it")
    failures = [*read_failures, *cursor_failures]

    return TranscriptProof(
        during_export=during_export,
        after_export=after_export,
        stable_unique_identities=stable_unique,
        pending_to_completed=pending_to_completed,
        compaction_observed=compaction_observed,
        appended_after_compaction=appended,
        retained_history_not_replayed=no_replay,
        failures=tuple(failures),
        read_failures=tuple(read_failures),
        cursor_failures=tuple(cursor_failures),
        repeated_live_exports=repeated_live_exports,
        repeated_after_exports=repeated_after_exports,
        compaction_pruned=compaction_pruned,
    )


def _stable_unique_identities(samples: tuple[TranscriptExportSample, ...]) -> bool:
    if len(samples) < 2:
        return False
    previous: set[MessagePartIdentity] | None = None
    for sample in samples:
        records = records_for_export(sample.export)
        identities = [record.identity for record in records]
        if len(identities) != len(set(identities)):
            return False
        current = set(identities)
        if previous is not None and not (previous & current):
            return False
        previous = current
    return True


def _has_pending_to_completed(samples: tuple[TranscriptExportSample, ...]) -> bool:
    pending: set[MessagePartIdentity] = set()
    for sample in samples:
        states = _tool_states(sample.export)
        if not sample.live and any(states.get(identity) == "completed" for identity in pending):
            return True
        if sample.live:
            pending.update(identity for identity, state in states.items() if state in {"pending", "running"})
    return False


def _tool_states(export: OpenCodeSessionExport) -> dict[MessagePartIdentity, str]:
    states: dict[MessagePartIdentity, str] = {}
    for message in export.messages:
        for part in message.parts:
            if part.state is not None:
                states[MessagePartIdentity(message.info.id, part.id)] = part.state.status
    return states


def _compaction_identities(export: OpenCodeSessionExport) -> set[MessagePartIdentity]:
    return {
        MessagePartIdentity(message.info.id, part.id)
        for message in export.messages
        for part in message.parts
        if part.type == "compaction"
    }


def _cursor_history(
    samples: tuple[TranscriptExportSample, ...],
) -> tuple[bool, bool, tuple[MessagePartIdentity, ...], bool]:
    cursor = MessagePartCursor.start()
    known: set[MessagePartIdentity] = set()
    removed: set[MessagePartIdentity] = set()
    seen_compaction: set[MessagePartIdentity] = set()
    appended_after_compaction: list[MessagePartIdentity] = []
    no_replay = True
    compaction_observed = False
    compaction_pruned = False
    compaction_seen = False
    for sample in samples:
        records = records_for_export(sample.export)
        known_before = set(known)
        read = cursor.admit(records)
        current_positions = {record.identity: index for index, record in enumerate(records)}
        compaction_identities = _compaction_identities(sample.export)
        new_compaction = compaction_identities - seen_compaction
        changed_compaction = any(
            admission.record.identity in compaction_identities and admission.kind in {"new", "updated"}
            for admission in read.admissions
        )
        compaction_positions = [
            current_positions[identity] for identity in compaction_identities if identity in current_positions
        ]
        marker_position = max(compaction_positions, default=-1)
        if new_compaction and known_before:
            compaction_observed = True
        if (new_compaction or changed_compaction) and known_before:
            if known_before - set(current_positions):
                compaction_pruned = True
            else:
                # OpenCode keeps historical rows but marks the model-replay head on compaction.
                # That logical prune is equivalent to physical removal for an export consumer.
                prior_message_ids = {identity.message_id for identity in known_before}
                if any(
                    part.tail_start_id in prior_message_ids
                    and any(identity.message_id != part.tail_start_id for identity in known_before)
                    for message in sample.export.messages
                    for part in message.parts
                    if part.type == "compaction" and part.tail_start_id is not None
                ):
                    compaction_pruned = True
        # The cursor's own marks are never pruned, so it can define replay away by construction.
        # A removed identity returning to the export is the signal it cannot suppress.
        returned = removed & current_positions.keys()
        if returned:
            no_replay = False
        removed -= returned
        removed |= known_before - current_positions.keys()
        for admission in read.admissions:
            identity = admission.record.identity
            if (
                admission.kind == "new"
                and identity not in known_before
                and identity not in compaction_identities
                and ((new_compaction and current_positions.get(identity, -1) > marker_position >= 0) or compaction_seen)
            ):
                appended_after_compaction.append(identity)
        known.update(record.identity for record in records)
        seen_compaction.update(compaction_identities)
        compaction_seen = compaction_seen or bool(new_compaction)
        cursor = read.cursor
    return compaction_observed, compaction_pruned, tuple(dict.fromkeys(appended_after_compaction)), no_replay


def _identity_payload(identity: MessagePartIdentity) -> dict[str, str | None]:
    return {"message_id": identity.message_id, "part_id": identity.part_id}


__all__ = ["TranscriptExportSample", "TranscriptProof", "inspect_transcript"]
