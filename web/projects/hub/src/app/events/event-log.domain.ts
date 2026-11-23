// bzh:frontend-domain-boundary — an entry in the hub's operational event log; no wire, no Angular.

/** A vocabulary value this build does not list, kept in its original spelling. */
export interface UnknownEventValue {
  readonly kind: 'unknown';
  readonly raw: string;
}

/** The severities this build knows, in ascending order. */
export const EVENT_SEVERITIES = ['info', 'warning', 'critical'] as const;

/** How loudly an event asks for attention. */
export type EventSeverity = KnownEventSeverity | UnknownEventValue;

/** A severity this build lists — the only values a severity filter can hold. */
export type KnownEventSeverity = (typeof EVENT_SEVERITIES)[number];

/** `value` narrowed to a listed severity, or `null` outside the closed set — so a filter value crossing from a
 * generic UI string narrows against the vocabulary this build knows. */
export function narrowEventSeverity(value: string): KnownEventSeverity | null {
  return EVENT_SEVERITIES.find((severity) => severity === value) ?? null;
}

/** A severity as text — a known value as itself, an unknown one raw. */
export function eventSeverityText(severity: EventSeverity): string {
  return typeof severity === 'string' ? severity : severity.raw;
}

/**
 * One event-log entry. A negative `id` marks an entry the hub projects rather than stores (an escalation); its
 * identity is the fingerprint over the raw text of its fields, so every hashed field is kept exactly as served.
 */
export interface EventLogEntry {
  readonly id: number;
  readonly recordedAt: string;
  readonly severity: EventSeverity;
  readonly kind: string;
  readonly message: string;
  readonly runnerId: string | null;
  readonly runnerName: string | null;
  readonly chunkId: string | null;
  readonly leaseId: string | null;
  readonly nodeName: string | null;
  readonly detail: Readonly<Record<string, unknown>> | null;
}
