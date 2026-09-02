import { injectQuery } from '@tanstack/angular-query-experimental';

import { getFindingApiFindingsFindingIdGet, type FindingView } from '../api/hub';
import { hubFindingsKey } from '../query-keys';

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
 *
 * A finding that fails to read (a 404, or any other error) is dropped from the joined
 * result rather than failing every other finding's row — one id going stale never
 * blanks the whole evidence table.
 */
export function injectHubFindingsQuery(findingIds: () => readonly string[]) {
  return injectQuery(() => {
    const ids = findingIds();
    return {
      queryKey: hubFindingsKey(ids),
      enabled: ids.length > 0,
      queryFn: async (): Promise<FindingView[]> => {
        const results = await Promise.allSettled(
          ids.map(async (id) => {
            const { data, error } = await getFindingApiFindingsFindingIdGet({
              path: { finding_id: id },
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
