import type { EventLogEntry } from './event-log.domain';

/** Distinct ids plus the active selection, sorted, including a lone scoped option. */
export function filterUniverse(ids: readonly string[], active: string | null): readonly string[] {
  const distinct = new Set(ids);
  if (active) distinct.add(active);
  return [...distinct].sort();
}

/** The runner-id chip universe over `events`, falsy ids stripped, with `selected` kept in it ({@link filterUniverse}). */
export function eventRunnerIds(
  events: readonly Pick<EventLogEntry, 'runnerId'>[],
  selected: string | null,
): readonly string[] {
  return filterUniverse(
    events.map((e) => e.runnerId).filter((r): r is string => !!r),
    selected,
  );
}

/** Each runner's name as `events` carry it, by id — the runner filter chips' labels. */
export function eventRunnerNames(
  events: readonly Pick<EventLogEntry, 'runnerId' | 'runnerName'>[],
): ReadonlyMap<string, string> {
  const names = new Map<string, string>();
  for (const e of events) if (e.runnerId && e.runnerName) names.set(e.runnerId, e.runnerName);
  return names;
}

/** The chunk-id chip universe over `events`, falsy ids stripped, with `selected` kept in it ({@link filterUniverse}). */
export function eventChunkIds(
  events: readonly Pick<EventLogEntry, 'chunkId'>[],
  selected: string | null,
): readonly string[] {
  return filterUniverse(
    events.map((e) => e.chunkId).filter((c): c is string => !!c),
    selected,
  );
}
