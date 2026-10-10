import { injectQuery } from '@tanstack/angular-query-experimental';

import { type ProjectView, getProjectApiProjectsProjectGet, hubProjectsKey, listProjectsApiProjectsGet } from 'fleet/shell';

/** How long a read of the tenant's projects stands before a lens resolution or the lens control reads it again. */
export const PROJECTS_STALE_MS = 30_000;

/** `GET /api/projects` — every project of the tenant the board is in. */
export async function readProjects(): Promise<readonly ProjectView[]> {
  const { data, error } = await listProjectsApiProjectsGet({ throwOnError: false });
  if (error) throw error;
  return data?.projects ?? [];
}

/** The tenant's projects as one query, shared by the lens control, the lens guard and every view that names a
 * project — keyed by `hubProjectsKey`, so a config write refreshes it. */
export function projectsQueryOptions() {
  return { queryKey: hubProjectsKey, queryFn: readProjects, staleTime: PROJECTS_STALE_MS };
}

/** The tenant's projects, read through {@link projectsQueryOptions} while `enabled()` holds. */
export function injectProjectsQuery(enabled: () => boolean = () => true) {
  return injectQuery(() => ({ ...projectsQueryOptions(), enabled: enabled() }));
}

/**
 * `GET /api/projects/{project}` — the project `segment` names, as an id, its current slug or a slug it has left; `null`
 * when the tenant holds no such project (the hub's `404`). Any other failure throws.
 */
export async function readProject(segment: string): Promise<ProjectView | null> {
  const { data, error, response } = await getProjectApiProjectsProjectGet({
    path: { project: segment },
    throwOnError: false,
  });
  if (response?.status === 404) return null;
  if (error) throw error;
  return data ?? null;
}
