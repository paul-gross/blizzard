import { injectQuery } from '@tanstack/angular-query-experimental';

import { getFindingApiFindingsFindingIdGet, type FindingView } from '../api/hub';
import { hubFindingKey } from '../query-keys';

/**
 * Hub `GET /api/findings/{finding_id}` read — one finding's full record. A garden
 * proposal carries finding *ids* only (`GardenProposalView.findings`), so the docket
 * detail's evidence table reads each one live rather than trusting a copy the
 * proposal itself might carry (`blizzard-product:/plans/garden/user-interface.md`
 * §The docket). Reactive over the selected finding id, `graphs.query.ts`'s own
 * `injectHubGraphQuery` null-tolerant conditional-query shape.
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
