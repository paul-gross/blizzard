"""The event-log kind census — every member of
:data:`~blizzard.foundation.event_log.EventLogKind` mapped to where it is emitted and
whether it lands as a stored ``event_log`` row or a synthetic projection, borrowing only
``tests/runner_event_census.py``'s two-disposition dataclass shape over an unrelated
vocabulary: this census's subject is :data:`EventLogKind`, not
:class:`~blizzard.runner.stores.IWriteRunnerStore`, and it names no member of
``WRITE_PROTOCOL_CENSUS`` or the SSE broker's ``EVENT_TYPES``. Exhaustiveness is carried by
``tests/test_event_log_kind_census.py``, this module's only reader — which is also why it
lives under ``tests/``, not ``src/``: no runtime importer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Recorded:
    """This kind lands as a stored ``event_log`` row, written from ``where`` — and from each of
    ``also``, a site in the other daemon recording the same kind."""

    where: str
    severity: str
    also: tuple[str, ...] = ()


@dataclass(frozen=True)
class Projected:
    """This kind never lands as a stored row — it is synthesized at read time from
    ``where``, carrying ``severity`` for the feed's own sort."""

    where: str
    severity: str


Disposition = Recorded | Projected

#: One entry per :data:`~blizzard.foundation.event_log.EventLogKind` member.
EVENT_LOG_KIND_CENSUS: dict[str, Disposition] = {
    "needs-human": Projected("hub/domain/work.py:EventFeed._projected", "critical"),
    "worker-lost": Recorded("runner/loop/attempt.py:Attempt.fail", "critical"),
    "owner-unresolvable": Recorded("runner/loop/attempt.py:Attempt.escalate_owner_unresolvable", "critical"),
    "no-acceptable-harness": Recorded("runner/loop/attempt.py:Attempt.escalate_no_acceptable_harness", "critical"),
    "hub-node-unroutable-outcome": Recorded("hub/delivery/hub_node.py:HubNodeExecutor._route", "critical"),
    "attempt-failed": Recorded("runner/loop/attempt.py:Attempt.fail", "warning"),
    "command-failed": Recorded("runner/loop/outbound.py:OutboundFacts.command_failed", "warning"),
    "work-item-close-failed": Recorded("hub/domain/work_closure.py:CloseIntentDrainer.sweep", "warning"),
    "transcript-truncated": Recorded("runner/loop/outbound.py:OutboundFacts.transcript_truncated", "warning"),
    "transcript-sidechain-dropped": Recorded(
        "runner/loop/transcript_pump.py:TranscriptPump._warn_sidechains_dropped", "warning"
    ),
    "worker-context-warned": Recorded("runner/loop/steps.py:ContextSample._event", "warning"),
    "attempt-abandoned": Recorded("runner/loop/attempt.py:Attempt.fail", "info"),
    "work-item-closed": Recorded("hub/domain/work_closure.py:CloseIntentDrainer.sweep", "info"),
    "trace-export-failed": Recorded(
        "hub/domain/tracing/sweep.py:TraceExportSweep.sweep",
        "warning",
        ("runner/domain/tracing/sweep.py:LeaseTraceSweep._failed",),
    ),
    "trace-export-recovered": Recorded(
        "hub/domain/tracing/sweep.py:TraceExportSweep.sweep",
        "info",
        ("runner/domain/tracing/sweep.py:LeaseTraceSweep._recovered",),
    ),
    "trace-window-skipped": Recorded(
        "hub/domain/tracing/sweep.py:TraceExportSweep.sweep",
        "warning",
        ("runner/domain/tracing/sweep.py:LeaseTraceSweep._jump",),
    ),
    "trace-config-rejected": Recorded(
        "hub/app.py:_announce_rejected_tracing",
        "warning",
        ("runner/domain/tracing/sweep.py:announce_rejected_tracing",),
    ),
    "egress-write-failed": Recorded("hub/domain/egress/sweep.py:EgressSweep._failed", "warning"),
    "egress-write-recovered": Recorded("hub/domain/egress/sweep.py:EgressSweep._recovered", "info"),
    "egress-config-rejected": Recorded("hub/app.py:_announce_rejected_egress", "warning"),
    "egress-cursor-reset": Recorded("hub/domain/egress/reset.py:EgressReset.reset", "info"),
}
