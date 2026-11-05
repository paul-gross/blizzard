"""Override keys the shared test factories accept, one ``TypedDict`` per model several test files build.

Each is ``total=False``: a factory owns the defaults for the keys a test does not name, and the dict carries only the
keys a call site overrides. A factory takes it as ``**overrides: Unpack[...]`` and applies it through the model's own
constructor, so pyright checks every key and value against the model's fields."""

from __future__ import annotations

from datetime import datetime
from typing import TypedDict

from blizzard.runner.leases.model import WorkRefStamp


class NewLeaseFields(TypedDict, total=False):
    lease_id: str
    chunk_id: str
    graph_id: str
    node_id: str
    node_name: str
    epoch: int
    retries_max: int
    created_at: datetime
    session_name: str | None
    resolved_model: str | None
    resolved_effort: str | None
    resolved_compaction_window: str | None
    graph_name: str | None
    work_refs: tuple[WorkRefStamp, ...] | None


class LeaseFields(NewLeaseFields, total=False):
    pid: int | None
    process_start_time: str | None
    session_id: str | None
    harness_id: str | None
    pgid: int | None


class TranscriptSliceFields(TypedDict, total=False):
    segment_id: str
    chunk_id: str
    node_id: str
    epoch: int
    spawn_generation: int
    runner_id: str
    turn_range_start: int
    turn_range_end: int
    final: bool
    normalizer_version: str
    harness_version: str | None
    record_truncated: bool
    turns_json: str
    supersedes: str | None
    harness_id: str | None
    model: str | None
    effort: str | None
    spawn_cwd: str | None
