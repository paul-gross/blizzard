// bzh:frontend-domain-boundary — the one place the event-log wire is read.
import { hubApi, openLookup, type EventView } from 'fleet';

import type { EventLogEntry, EventSeverity, KnownEventSeverity } from './event-log.domain';

/** An event's severity; a value this build does not list reads as unknown, raw. */
export const eventSeverity = openLookup<hubApi.EventLogSeverity, EventSeverity>(
  { info: 'info', warning: 'warning', critical: 'critical' },
  (raw) => ({ kind: 'unknown', raw }),
);

const WIRE_SEVERITY: Readonly<Record<KnownEventSeverity, hubApi.EventLogSeverity>> = {
  info: hubApi.EventLogSeverity.INFO,
  warning: hubApi.EventLogSeverity.WARNING,
  critical: hubApi.EventLogSeverity.CRITICAL,
};

/** A severity filter as `GET /api/events`'s own `severity` query param. */
export function toEventLogSeverity(severity: KnownEventSeverity): hubApi.EventLogSeverity {
  return WIRE_SEVERITY[severity];
}

/** An event-log row as the domain holds it, every field the projected-event fingerprint hashes kept as served. */
export function fromEventView(wire: EventView): EventLogEntry {
  return {
    id: wire.id,
    recordedAt: wire.recorded_at,
    severity: eventSeverity(wire.severity),
    kind: wire.kind,
    message: wire.message,
    runnerId: wire.runner_id,
    runnerName: wire.runner_name,
    chunkId: wire.chunk_id,
    leaseId: wire.lease_id,
    nodeName: wire.node_name,
    detail: wire.detail,
  };
}
