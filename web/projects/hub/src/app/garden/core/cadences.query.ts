import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  type CadenceErrandView,
  type CadenceView,
  listCadenceErrandsApiCadencesCadenceIdErrandsGet,
  listCadencesApiCadencesGet,
  listProjectCadencesApiProjectsProjectCadencesGet,
} from 'fleet';
import { injectProjectFilter, lensedKey } from 'fleet/shell';

/** The cadence list's key — `GET /api/projects/{project}/cadences`, or `GET /api/cadences` on All. Cadences carry no
 * SSE event of their own, so only a cadence write invalidates it. */
export const hubCadencesKey = ['hub', 'cadences'] as const;

/** One cadence's errands — `GET /api/cadences/{cadence_id}/errands`. */
export function hubCadenceErrandsKey(cadenceId: string | null): readonly unknown[] {
  return ['hub', 'cadence-errands', cadenceId];
}

/**
 * The cadence list — every cadence, newest first, retired ones included and marked, each carrying its attention, its
 * errands counted by state, and its next moments. Under a project lens it is that project's cadences; on All it is the
 * tenant's. Read by the cadences tab, its detail, and any garden surface naming the cadences that keep a routine.
 */
export function injectHubCadencesQuery() {
  const project = injectProjectFilter();
  return injectQuery(() => {
    const lens = project();
    return {
      queryKey: lensedKey(hubCadencesKey, lens),
      queryFn: async (): Promise<CadenceView[]> => {
        const { data, error } =
          lens === null
            ? await listCadencesApiCadencesGet({ query: { include_retired: true }, throwOnError: false })
            : await listProjectCadencesApiProjectsProjectCadencesGet({
                path: { project: lens },
                query: { include_retired: true },
                throwOnError: false,
              });
        if (error) throw error;
        return data ?? [];
      },
    };
  });
}

/**
 * One cadence's errands — each chore over each scope, with its state, the attention stopping it, its last run, and
 * when it is next due. Disabled while `cadenceId()` is `null`.
 */
export function injectHubCadenceErrandsQuery(cadenceId: () => string | null) {
  return injectQuery(() => {
    const id = cadenceId();
    return {
      queryKey: hubCadenceErrandsKey(id),
      enabled: id !== null,
      queryFn: async (): Promise<CadenceErrandView[]> => {
        const { data, error } = await listCadenceErrandsApiCadencesCadenceIdErrandsGet({
          path: { cadence_id: id! },
          throwOnError: false,
        });
        if (error) throw error;
        return data ?? [];
      },
    };
  });
}
