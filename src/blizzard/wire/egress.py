"""Fact-egress operator wire bodies — ``GET /api/egress/status``, ``POST /api/egress/reset`` and
``POST /api/egress/backfill``."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class EgressDatasetStatus(BaseModel):
    """One configured dataset: ``cursor_at`` is its position in time, ``None`` before its first pass;
    ``lag_seconds`` is the oldest waiting row's age, ``None`` when nothing waits."""

    name: str
    cursor_at: str | None
    lag_seconds: float | None


class EgressStatusResponse(BaseModel):
    """Whether the fact export runs and how it is doing. ``directory`` and ``format`` are set whenever a directory
    is configured; a ``rejected`` export names the setting and value. ``last_pass_*`` is the newest cursor row of any
    dataset, ``last_file`` the last data file written (never a manifest). ``free_bytes`` is ``None`` when the export
    is not on or the directory cannot be read. ``backfill_max_window_seconds`` is the widest window one backfill
    may write."""

    state: Literal["on", "off", "rejected"]
    rejected_setting: str | None
    rejected_value: str | None
    directory: str | None
    format: str | None
    datasets: list[EgressDatasetStatus]
    last_pass_at: str | None
    last_pass_dataset: str | None
    last_pass_row_count: int | None
    last_file: str | None
    last_error_at: str | None
    last_error_message: str | None
    last_error_ongoing: bool
    free_bytes: int | None
    min_free_bytes: int | None
    backfill_max_window_seconds: int


class EgressResetRequest(BaseModel):
    """Move ``dataset``'s cursor to ``to``, an instant not in the future: forward skips the window between, back
    repeats it."""

    dataset: str
    to: datetime


class EgressResetResponse(BaseModel):
    """Where the cursor stood (``None`` before the dataset's first pass), where it stands now, and whether the
    window between was ``skipped`` or ``repeated``."""

    dataset: str
    from_at: str | None
    to_at: str
    direction: Literal["skipped", "repeated"]


class EgressBackfillRequest(BaseModel):
    """The half-open window ``[since, until)`` to write again, for one ``dataset`` or all configured.
    ``dry_run`` counts what would be written and writes nothing."""

    since: datetime
    until: datetime
    dataset: str | None = None
    dry_run: bool = False


class EgressBackfillCount(BaseModel):
    """One dataset's rows and data files — written, or with ``dry_run`` that would be."""

    dataset: str
    rows: int
    files: int


class EgressBackfillResponse(BaseModel):
    """What a backfill wrote, or with ``dry_run`` would have written, per dataset."""

    dry_run: bool
    datasets: list[EgressBackfillCount]


class EgressBackfillFailure(BaseModel):
    """The writer refused or raised: ``datasets`` count what was committed before the backfill stopped, and
    ``cause`` names why, as the live export's own failure events do."""

    detail: str
    cause: str
    datasets: list[EgressBackfillCount]
