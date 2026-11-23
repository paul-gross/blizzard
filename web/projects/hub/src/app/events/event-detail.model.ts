import { asyncState, type AsyncStateQuery, type KitAsyncStateValue } from 'fleet';
import { eventSeverityText, type EventLogEntry } from './event-log.domain';

/** Stable JSON for wire payloads, independent of object key order. */
function canonical(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null';
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  const object = value as Record<string, unknown>;
  return `{${Object.keys(object).sort().map((key) => `${JSON.stringify(key)}:${canonical(object[key])}`).join(',')}}`;
}

/** The projection's id and runner display name can change without changing the event. */
export async function eventFingerprint(event: EventLogEntry): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null;
  const payload = canonical([
    event.recordedAt, event.kind, event.chunkId, eventSeverityText(event.severity), event.runnerId,
    event.leaseId, event.nodeName, event.message, event.detail,
  ]);
  try {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(payload));
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  } catch {
    return null;
  }
}

export async function eventFingerprints(events: readonly EventLogEntry[]): Promise<ReadonlyMap<EventLogEntry, string>> {
  const entries = await Promise.all(events.filter((event) => event.id < 0).map(async (event) =>
    [event, await eventFingerprint(event)] as const,
  ));
  return new Map(entries.filter((entry): entry is readonly [EventLogEntry, string] => entry[1] !== null));
}

/** Refuse projected events unless their full payload fingerprint resolves uniquely. */
export async function findEvent(
  events: readonly EventLogEntry[], id: string | null, at: string | null, kind: string | null, chunk: string | null,
  fingerprint: string | null,
): Promise<EventLogEntry | null> {
  if (!id || !at || !kind) return null;
  const matches = events.filter((event) =>
    event.recordedAt === at && event.kind === kind && event.chunkId === chunk &&
    (Number(id) < 0 ? event.id < 0 : String(event.id) === id),
  );
  if (Number(id) >= 0) return matches.length === 1 ? matches[0] : null;
  if (!fingerprint || !/^[a-f0-9]{64}$/.test(fingerprint)) return null;
  const identities = await Promise.all(matches.map(eventFingerprint));
  if (identities.includes(null)) return null;
  const verified = matches.filter((_, index) => identities[index] === fingerprint);
  return verified.length === 1 ? verified[0] : null;
}

export function detailState(query: AsyncStateQuery, loading: boolean, event: EventLogEntry | null): KitAsyncStateValue {
  if (query.isPending() || loading) return 'loading';
  return asyncState(query, event === null);
}
