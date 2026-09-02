import { injectQuery } from '@tanstack/angular-query-experimental';

import { getWorkItemApiWorkSourcesSourceItemsRefGet, type WorkItemView } from '../api/hub';
import { hubWorkItemKey } from '../query-keys';

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
