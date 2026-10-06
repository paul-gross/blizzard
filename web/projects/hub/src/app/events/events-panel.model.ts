/** Distinct ids ∪ the active selection, sorted — or `[]` when there is nothing worth
 * filtering (≤1 distinct id and no active selection), which hides the chip row. */
export function filterUniverse(ids: readonly string[], active: string | null): readonly string[] {
  const distinct = new Set(ids);
  if (active) distinct.add(active);
  if (distinct.size < 2 && active === null) return [];
  return [...distinct].sort();
}

/** The runner-id chip universe over `events`, falsy ids stripped, with `selected` kept in it ({@link filterUniverse}). */
export function eventRunnerIds(
  events: readonly { readonly runner_id?: string | null }[],
  selected: string | null,
): readonly string[] {
  return filterUniverse(
    events.map((e) => e.runner_id).filter((r): r is string => !!r),
    selected,
  );
}

/** Each runner's name as `events` carry it, by id — the runner filter chips' labels. */
export function eventRunnerNames(
  events: readonly { readonly runner_id?: string | null; readonly runner_name?: string | null }[],
): ReadonlyMap<string, string> {
  const names = new Map<string, string>();
  for (const e of events) if (e.runner_id && e.runner_name) names.set(e.runner_id, e.runner_name);
  return names;
}

/** The chunk-id chip universe over `events`, falsy ids stripped, with `selected` kept in it ({@link filterUniverse}). */
export function eventChunkIds(
  events: readonly { readonly chunk_id?: string | null }[],
  selected: string | null,
): readonly string[] {
  return filterUniverse(
    events.map((e) => e.chunk_id).filter((c): c is string => !!c),
    selected,
  );
}
