// bzh:frontend-domain-boundary — the one place the event-log wire is read.
import { hubApi, openLookup, type EventView } from 'fleet';

import type { EventLogEntry, EventSeverity } from './event-log.domain';

/** An event's severity; a value this build does not list reads as unknown, raw. */
export const eventSeverity = openLookup<hubApi.EventLogSeverity, EventSeverity>(
  { info: 'info', warning: 'warning', critical: 'critical' },
  (raw) => ({ kind: 'unknown', raw }),
);

/** `value` narrowed to the generated severity vocabulary (`GET /api/events`'s own query
 * param), or `null` outside the closed set — so a filter value crossing from a generic UI
 * string into the typed query narrows against the wire's own values. */
export function narrowEventSeverity(value: string): hubApi.EventLogSeverity | null {
  return Object.values(hubApi.EventLogSeverity).find((severity) => severity === value) ?? null;
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
