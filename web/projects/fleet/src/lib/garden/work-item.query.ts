import { injectQuery } from '@tanstack/angular-query-experimental';

import { getWorkItemApiWorkSourcesSourceItemsRefGet, type WorkItemView } from '../api/hub';
import { hubWorkItemKey, hubWorkItemsKey } from '../query-keys';

/**
 * Hub `GET /api/work-sources/{source}/items/{ref}` read — an accepted proposal's
 * linked work item, resolved through its closure's `source`/`ref` pointer rather
 * than the accept response's `chunk_id` (Decision 4: `chunk_id` rides only
 * `GardenProposalAcceptResponse` and does not persist, so the stored closure's
 * pointer is what a later read has to work with). `label`/`web_url` come from this
 * view rather than being guessed; `web_url` is `null` once the chunk is terminal, so
 * the caller degrades to the label instead of a dead link. Reactive over the
 * selected (source, ref) pair, `graphs.query.ts`'s own `injectHubGraphQuery`
 * null-tolerant conditional-query shape.
 */
export function injectHubWorkItemQuery(source: () => string | null, ref: () => string | null) {
  return injectQuery(() => {
    const s = source();
    const r = ref();
    return {
      queryKey: hubWorkItemKey(s, r),
      enabled: s !== null && r !== null,
      queryFn: async (): Promise<WorkItemView> => {
        const { data, error } = await getWorkItemApiWorkSourcesSourceItemsRefGet({
          path: { source: s!, ref: r! },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
    };
  });
}

/** One work item pointer — `source`/`ref`, the same pair {@link injectHubWorkItemQuery}
 * takes, named here for the fan-out read's own use. */
export interface WorkItemPointer {
  readonly source: string;
  readonly ref: string;
}

/**
 * The findings triage bucket's linked-work-items read — every pointer in
 * `pointers()`, resolved live through `GET /api/work-sources/{source}/items/{ref}` and
 * joined into one list, {@link injectHubFindingsQuery}'s own (`finding.query.ts`)
 * Promise.allSettled fan-out-and-join shape: a pointer that fails to read (a 404, or
 * any other error) is dropped from the joined result rather than failing every other
 * pointer's row. `pointers` is a function, reactive over the selected routine/scope —
 * the bucket's own findings determine which work items this fans out to, and that set
 * changes per selection.
 */
export function injectHubWorkItemsQuery(pointers: () => readonly WorkItemPointer[]) {
  return injectQuery(() => {
    const ps = pointers();
    return {
      queryKey: hubWorkItemsKey(ps.map((p): readonly [string, string] => [p.source, p.ref])),
      enabled: ps.length > 0,
      queryFn: async (): Promise<WorkItemView[]> => {
        const results = await Promise.allSettled(
          ps.map(async (p) => {
            const { data, error } = await getWorkItemApiWorkSourcesSourceItemsRefGet({
              path: { source: p.source, ref: p.ref },
              throwOnError: false,
            });
            if (error) throw error;
            return data!;
          }),
        );
        return results.filter((r) => r.status === 'fulfilled').map((r) => r.value);
      },
    };
  });
}
