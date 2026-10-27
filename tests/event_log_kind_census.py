"""The event-log kind census — every member of
:data:`~blizzard.foundation.event_log.EventLogKind` mapped to where it is emitted and
whether it lands as a stored ``event_log`` row, a synthetic projection, or a row of the hub's own
event log alone; and every member of :data:`~blizzard.hub.domain.observability.hub_events.HubEventKind` mapped to
the sites recording it in ``hub_event_log``. It borrows only
``tests/runner_event_census.py``'s dataclass-disposition shape over an unrelated
vocabulary: this census's subject is the event kinds, not
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


@dataclass(frozen=True)
class HubOnly:
    """This kind is the hub's own and never lands as an ``event_log`` row: it is recorded in ``hub_event_log`` from
    ``where`` (:data:`HUB_EVENT_KIND_CENSUS` names every site)."""

    where: str
    severity: str


Disposition = Recorded | Projected | HubOnly

#: One entry per :data:`~blizzard.foundation.event_log.EventLogKind` member.
EVENT_LOG_KIND_CENSUS: dict[str, Disposition] = {
    "needs-human": Projected("hub/domain/chunk/model.py:EventFeed._projected", "critical"),
    "worker-lost": Recorded("runner/lifecycle/attempt.py:Attempt.fail", "critical"),
    "owner-unresolvable": Recorded("runner/lifecycle/attempt.py:Attempt.escalate_owner_unresolvable", "critical"),
    "no-acceptable-harness": Recorded("runner/lifecycle/attempt.py:Attempt.escalate_no_acceptable_harness", "critical"),
    "hub-node-unroutable-outcome": Recorded("hub/delivery/hub_node.py:HubNodeExecutor._route", "critical"),
    "hub-action-overran": Recorded("hub/delivery/hub_node.py:HubNodeExecutor._record_overrun", "warning"),
    "hub-action-missing": Recorded("hub/delivery/hub_node.py:HubNodeExecutor._record_action_missing", "critical"),
    "repository-unresolved": Recorded("hub/delivery/hub_node.py:HubNodeExecutor._record_refusal_event", "critical"),
    "repository-not-linked": Recorded("hub/delivery/hub_node.py:HubNodeExecutor._record_refusal_event", "critical"),
    "forge-capability-missing": Recorded(
        "hub/delivery/hub_node.py:HubNodeExecutor._record_capability_missing", "warning"
    ),
    "config-old-field-used": Recorded("hub/domain/config/forge_resolution.py:record_old_fields", "warning"),
    "attempt-failed": Recorded("runner/lifecycle/attempt.py:Attempt.fail", "warning"),
    "command-failed": Recorded("runner/hub/outbound.py:OutboundFacts.command_failed", "warning"),
    "work-item-close-failed": Recorded("hub/domain/work_items/closure.py:CloseIntentDrainer.drain", "warning"),
    "transcript-truncated": Recorded("runner/hub/outbound.py:OutboundFacts.transcript_truncated", "warning"),
    "transcript-sidechain-dropped": Recorded(
        "runner/transcripts/transcript_pump.py:TranscriptPump._warn_sidechains_dropped", "warning"
    ),
    "worker-context-warned": Recorded("runner/loop/steps.py:ContextSample._event", "warning"),
    "attempt-abandoned": Recorded("runner/lifecycle/attempt.py:Attempt.fail", "info"),
    "work-item-closed": Recorded("hub/domain/work_items/closure.py:CloseIntentDrainer.drain", "info"),
    "trace-export-failed": Recorded("runner/tracing/sweep.py:LeaseTraceSweep._failed", "warning"),
    "trace-export-recovered": Recorded("runner/tracing/sweep.py:LeaseTraceSweep._recovered", "info"),
    "trace-window-skipped": Recorded("runner/tracing/sweep.py:LeaseTraceSweep._jump", "warning"),
    "trace-config-rejected": Recorded("runner/tracing/sweep.py:announce_rejected_tracing", "warning"),
    "egress-write-failed": HubOnly("hub/domain/observability/egress/sweep.py:EgressSweep._failed", "warning"),
    "egress-write-recovered": HubOnly("hub/domain/observability/egress/sweep.py:EgressSweep._recovered", "info"),
    "egress-config-rejected": HubOnly("hub/app.py:_announce_rejected_egress", "warning"),
    "egress-cursor-reset": HubOnly("hub/domain/observability/egress/reset.py:EgressReset.reset", "info"),
}

#: One entry per :data:`~blizzard.hub.domain.observability.hub_events.HubEventKind` member: every site that records it in
#: ``hub_event_log`` through the hub's event log service. A tenant teardown's kinds have no writer in this hub.
HUB_EVENT_KIND_CENSUS: dict[str, tuple[str, ...]] = {
    "trace-export-failed": ("hub/domain/observability/tracing/sweep.py:TraceExportSweep.sweep",),
    "trace-export-recovered": ("hub/domain/observability/tracing/sweep.py:TraceExportSweep.sweep",),
    "trace-window-skipped": ("hub/domain/observability/tracing/sweep.py:TraceExportSweep.sweep",),
    "trace-config-rejected": ("hub/app.py:_announce_rejected_tracing",),
    "egress-write-failed": ("hub/domain/observability/egress/sweep.py:EgressSweep._failed",),
    "egress-write-recovered": ("hub/domain/observability/egress/sweep.py:EgressSweep._recovered",),
    "egress-config-rejected": ("hub/app.py:_announce_rejected_egress",),
    "egress-cursor-reset": ("hub/domain/observability/egress/reset.py:EgressReset.reset",),
    "hub-started": ("hub/app.py:_announce_started",),
    "hub-migrated": ("hub/runtime.py:Runtime.init", "hub/runtime.py:Runtime.migrate"),
    "tenant-closed": (),
    "tenant-deleted": (),
}
