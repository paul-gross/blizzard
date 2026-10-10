import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  getProjectApiProjectsProjectGet,
  hubProjectKey,
  hubProjectLinksKey,
  listRepositoryLinksApiProjectsProjectRepositoryLinksGet,
  listSourceLinksApiProjectsProjectSourceLinksGet,
  type ProjectView,
  type RepositoryLinkView,
  type SourceLinkView,
} from 'fleet';

/** `GET /api/projects/{project}` — one project by id, current slug or former slug, at rest
 * while `project()` is `null`. Keyed under `hubProjectsKey`, so a project write refreshes it. */
export function injectProjectQuery(project: () => string | null) {
  return injectQuery(() => {
    const key = project();
    return {
      queryKey: hubProjectKey(key),
      enabled: key !== null,
      queryFn: async (): Promise<ProjectView> => {
        const { data, error } = await getProjectApiProjectsProjectGet({
          path: { project: key! },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
    };
  });
}

/** `GET /api/projects/{project}/source-links` — the project's source links, unlinked ones
 * included (`linked: false`), and the built-in source's. At rest while `projectId()` —
 * the project's id or slug — is `null`. */
export function injectProjectSourceLinksQuery(projectId: () => string | null) {
  return injectQuery(() => {
    const id = projectId();
    return {
      queryKey: hubProjectLinksKey(id, 'source'),
      enabled: id !== null,
      queryFn: async (): Promise<SourceLinkView[]> => {
        const { data, error } = await listSourceLinksApiProjectsProjectSourceLinksGet({
          path: { project: id! },
          throwOnError: false,
        });
        if (error) throw error;
        return data?.source_links ?? [];
      },
    };
  });
}

/** `GET /api/projects/{project}/repository-links` — the project's repository links, unlinked
 * ones included. At rest while `projectId()` —
 * the project's id or slug — is `null`. */
export function injectProjectRepositoryLinksQuery(projectId: () => string | null) {
  return injectQuery(() => {
    const id = projectId();
    return {
      queryKey: hubProjectLinksKey(id, 'repository'),
      enabled: id !== null,
      queryFn: async (): Promise<RepositoryLinkView[]> => {
        const { data, error } = await listRepositoryLinksApiProjectsProjectRepositoryLinksGet({
          path: { project: id! },
          throwOnError: false,
        });
        if (error) throw error;
        return data?.repository_links ?? [];
      },
    };
  });
}
