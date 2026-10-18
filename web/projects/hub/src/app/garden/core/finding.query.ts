import { injectInfiniteQuery, injectQuery } from '@tanstack/angular-query-experimental';

import { getFindingApiFindingsFindingIdGet, listFindingClassesApiFindingsClassesGet, listFindingsApiFindingsGet, listProjectFindingClassesApiProjectsProjectFindingsClassesGet, listProjectFindingsApiProjectsProjectFindingsGet, type FindingDetailView, type FindingView, type FindingsPageView, hubApi, hubFindingKey, hubFindingClassesKey, hubFindingsBucketKey, hubFindingsKey } from 'fleet';
import { injectProjectFilter, lensedKey } from 'fleet/shell';

const FINDINGS_PAGE = 50;

/**
 * Every id in `findingIds()`, read live through its own `GET
 * /api/findings/{finding_id}` — evidence is read live, one finding
 * at a time: a garden proposal carries finding *ids* only
 * (`GardenProposalView.findings`), so each is read here rather than from a copy
 * (`blizzard-product:/delivered/garden/user-interface.md` §The docket). One `injectQuery`
 * whose `queryFn` fans the id list out and joins it — the id list itself is reactive
 * (re-derived per selected proposal), and Angular's injection context cannot vary a
 * fixed number of `injectQuery` calls at runtime the way a per-id call would need.
 *
 * A finding that fails to read is dropped from the joined result.
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
 * Carries its own cache key ({@link hubFindingKey}); pinned by
 * `finding.query.spec.ts`'s "surfaces a failed single-finding read as an error, even
 * beside a fan-out over the same id".
 *
 * Stays disabled while `findingId()` is null — the caller's own "nothing selected"
 * rest state is branched before this read is consulted, `bzh:frontend-empty-state-gated`.
 *
 * Resolves `FindingDetailView` — `GET /api/findings/{finding_id}`'s
 * actual response, a superset of `FindingView` that adds the finding's whole
 * append-only fact chain (`facts`, oldest-first). {@link injectHubFindingsQuery}'s
 * fan-out keeps reading plain `FindingView` off the list endpoint; only the
 * one-finding read carries the chain.
 */
export function injectHubFindingQuery(findingId: () => string | null) {
  return injectQuery(() => {
    const id = findingId();
    return {
      queryKey: hubFindingKey(id),
      enabled: id !== null,
      queryFn: async (): Promise<FindingDetailView> => {
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
 * The triage bucket: one descending, hub-filtered page per request. The key carries
 * every filter and the project lens, while `next_cursor` advances only on demand.
 * `include_gone` lets an explicit state or All states reach exited rows; an absent
 * state is interpreted by the caller as live, not as an unfiltered request.
 */
export function injectHubFindingsBucketQuery(routine: () => string | null, scope: () => string | null, className: () => string | null, state: () => string | null) {
  const project = injectProjectFilter();
  return injectInfiniteQuery(() => {
    const r = routine();
    const s = scope();
    const lens = project();
    const cls = className();
    const st = state();
    const query = (cursor: string | undefined) => ({
      ...(r !== null ? { routine: r } : {}),
      ...(s !== null ? { scope: s } : {}),
      include_gone: true,
      descending: true,
      ...(cls !== null ? { class: cls } : {}),
      ...(st !== null ? { state: st as hubApi.FindingState } : {}),
      cursor,
      limit: FINDINGS_PAGE,
    });
    return {
      queryKey: lensedKey(hubFindingsBucketKey(r, s, cls, st), lens),
      initialPageParam: undefined as string | undefined,
      queryFn: async ({ pageParam }: { pageParam: string | undefined }): Promise<FindingsPageView> => {
        const { data, error } = lens === null
          ? await listFindingsApiFindingsGet({ query: query(pageParam), throwOnError: false })
          : await listProjectFindingsApiProjectsProjectFindingsGet({
              path: { project: lens }, query: query(pageParam), throwOnError: false,
            });
        if (error) throw error;
        return data!;
      },
      getNextPageParam: (last: FindingsPageView) => last.next_cursor ?? undefined,
    };
  });
}

export function injectHubFindingClassesQuery(routine: () => string | null, scope: () => string | null) {
  const project = injectProjectFilter();
  return injectQuery(() => {
    const r = routine();
    const s = scope();
    const lens = project();
    const query = { ...(r !== null ? { routine: r } : {}), ...(s !== null ? { scope: s } : {}) };
    return {
      queryKey: lensedKey(hubFindingClassesKey(r, s), lens),
      queryFn: async (): Promise<string[]> => {
        const { data, error } = lens === null
          ? await listFindingClassesApiFindingsClassesGet({ query, throwOnError: false })
          : await listProjectFindingClassesApiProjectsProjectFindingsClassesGet({ path: { project: lens }, query, throwOnError: false });
        if (error) throw error;
        return data!;
      },
    };
  });
}
