import { injectQuery } from '@tanstack/angular-query-experimental';

import { getFindingApiFindingsFindingIdGet, type FindingView } from '../api/hub';
import { hubFindingKey } from '../query-keys';

/**
 * Hub `GET /api/findings/{finding_id}` read — one finding's full record. Reactive
 * over the selected finding id, `graphs.query.ts`'s own `injectHubGraphQuery`
 * null-tolerant conditional-query shape.
 */
export function injectHubFindingQuery(findingId: () => string | null) {
  return injectQuery(() => {
    const id = findingId();
    return {
      queryKey: hubFindingKey(id),
      enabled: id !== null,
      queryFn: async (): Promise<FindingView> => {
        const { data, error } = await getFindingApiFindingsFindingIdGet({
          path: { finding_id: id! },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
    };
  });
}

/**
 * Every id in `findingIds()`, read live through its own `GET
 * /api/findings/{finding_id}` — Decision 3's own "evidence is read live, one finding
 * at a time": a garden proposal carries finding *ids* only
 * (`GardenProposalView.findings`), so the docket detail's evidence table reads each
 * one live rather than trusting a copy the proposal itself might carry
 * (`blizzard-product:/plans/garden/user-interface.md` §The docket). One `injectQuery`
 * whose `queryFn` fans the id list out and joins it — the id list itself is reactive
 * (re-derived per selected proposal), and Angular's injection context cannot vary a
 * fixed number of `injectQuery` calls at runtime the way a per-id call would need.
 */
export function injectHubFindingsQuery(findingIds: () => readonly string[]) {
  return injectQuery(() => {
    const ids = findingIds();
    return {
      queryKey: ['hub', 'findings', ...ids] as const,
      enabled: ids.length > 0,
      queryFn: async (): Promise<FindingView[]> => {
        const results = await Promise.all(
          ids.map((id) => getFindingApiFindingsFindingIdGet({ path: { finding_id: id }, throwOnError: false })),
        );
        return results.map(({ data, error }) => {
          if (error) throw error;
          return data!;
        });
      },
    };
  });
}
