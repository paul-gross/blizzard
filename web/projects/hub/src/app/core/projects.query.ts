import { type QueryClient, injectQuery } from '@tanstack/angular-query-experimental';

import {
  type ProjectNotFoundProblem,
  type ProjectView,
  getProjectApiProjectsProjectGet,
  listProjectsApiProjectsGet,
  problemType,
} from 'fleet/shell';
import { hubProjectsKey } from './query-keys';
import type { Project } from './project.domain';
import { fromProjectView } from './project.translate';

/** The `type` of the hub's refusal when the tenant holds no such project. */
const PROJECT_NOT_FOUND: ProjectNotFoundProblem['type'] =
  'urn:blizzard:problem:project-not-found';

/** How long a read of the tenant's projects stands before a lens resolution or the lens control reads it again. */
export const PROJECTS_STALE_MS = 30_000;

/** `GET /api/projects` — every project of the tenant the board is in. */
export async function readProjects(): Promise<readonly ProjectView[]> {
  const { data, error } = await listProjectsApiProjectsGet({
    throwOnError: false,
  });
  if (error) throw error;
  return data?.projects ?? [];
}

/** The tenant's projects as one query, shared by the lens control, the lens guard and every view that names a
 * project — keyed by `hubProjectsKey`, so a config write refreshes it. */
function projectsQueryOptions() {
  return {
    queryKey: hubProjectsKey,
    queryFn: readProjects,
    staleTime: PROJECTS_STALE_MS,
  };
}

/** Domain projection over the tenant's cached projects. */
export function projectDomainsQueryOptions() {
  return { ...projectsQueryOptions(), select: (projects: readonly ProjectView[]) => projects.map(fromProjectView) };
}

export function injectProjectDomainsQuery(enabled: () => boolean = () => true) {
  return injectQuery(() => ({ ...projectDomainsQueryOptions(), enabled: enabled() }));
}

/**
 * `GET /api/projects/{project}` — the project `segment` names, as an id, its current slug or a slug it has left; `null`
 * when the tenant holds no such project (the hub's `404`). Any other failure throws.
 */
export async function readProject(
  segment: string,
): Promise<ProjectView | null> {
  const { data, error } = await getProjectApiProjectsProjectGet({
    path: { project: segment },
    throwOnError: false,
  });
  if (problemType(error) === PROJECT_NOT_FOUND) return null;
  if (error) throw error;
  return data ?? null;
}

/** {@link readProject} as a {@link Project}. */
export async function readProjectDomain(segment: string): Promise<Project | null> {
  const project = await readProject(segment);
  return project === null ? null : fromProjectView(project);
}

/** The tenant's projects through `queryClient`'s cache, read once when it holds none — a guard's path to the list
 * {@link injectProjectDomainsQuery} hands a view. */
export async function ensureProjectDomains(queryClient: QueryClient): Promise<readonly Project[]> {
  const projects = await queryClient.ensureQueryData(projectsQueryOptions());
  return projects.map(fromProjectView);
}
