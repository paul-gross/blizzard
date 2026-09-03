import { injectQuery } from '@tanstack/angular-query-experimental';

import { getFindingApiFindingsFindingIdGet, listFindingsApiFindingsGet, type FindingView } from '../api/hub';
import { hubFindingKey, hubFindingsBucketKey, hubFindingsKey } from '../query-keys';

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
 * blanks the whole evidence table. That is a table's bargain, not a detail pane's:
 * a surface reading exactly one finding wants the failure, and takes
 * {@link injectHubFindingQuery} instead.
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

/**
 * One finding, read live through `GET /api/findings/{finding_id}` — the read a
 * surface takes when the finding it names is the whole surface, so a 404 or a 500
 * has to reach the reader as an error state.
 *
 * Deliberately not {@link injectHubFindingsQuery} with a one-element list: that
 * fan-out swallows a failed read to protect the docket's other evidence rows, which
 * on a single id turns "this finding could not be read" into a successful empty
 * result indistinguishable from "nothing is selected". Same endpoint, opposite
 * bargain, so it carries its own cache key ({@link hubFindingKey}).
 *
 * Stays disabled while `findingId()` is null — the caller's own "nothing selected"
 * rest state is branched before this read is consulted, `bzh:frontend-empty-state-gated`.
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
 * The findings triage bucket read — every finding for one routine+scope pair, live
 * through `GET /api/findings?routine=&scope=` (the triage surface, as distinct from
 * {@link injectHubFindingsQuery}'s by-id fan-out the docket detail's evidence table
 * reads). Both `routine` and `scope` are required by the server
 * (`ListFindingsApiFindingsGetData.query`), so the query stays disabled until both are
 * chosen, `work-item.query.ts`'s own null-tolerant disabled-query shape. Always reads
 * with `include_gone: true` — a gone finding still belongs on the triage surface until
 * a person confirms it (that's what `confirm-gone` records), so the bucket can't
 * afford to have the server drop it before a person has weighed in.
 */
export function injectHubFindingsBucketQuery(routine: () => string | null, scope: () => string | null) {
  return injectQuery(() => {
    const r = routine();
    const s = scope();
    return {
      queryKey: hubFindingsBucketKey(r, s),
      enabled: r !== null && s !== null,
      queryFn: async (): Promise<FindingView[]> => {
        const { data, error } = await listFindingsApiFindingsGet({
          query: { routine: r!, scope: s!, include_gone: true },
          throwOnError: false,
        });
        if (error) throw error;
        return data ?? [];
      },
    };
  });
}
