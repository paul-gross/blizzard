import { computed, type Signal } from '@angular/core';
import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  chunkCountsApiChunkCountsGet,
  type ChunkCountsView,
  getProjectApiProjectsProjectGet,
  hubChunkCountsKey,
  hubProjectsKey,
  hubProjectKey,
  hubProjectLinksKey,
  listRepositoryLinksApiProjectsProjectRepositoryLinksGet,
  lensedKey,
  listSourceLinksApiProjectsProjectSourceLinksGet,
  type ProjectView,
  type RepositoryLinkView,
  type SourceLinkView,
} from 'fleet';

import type { ProjectFigures } from './projects.model';

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

async function readSourceLinks(project: string): Promise<SourceLinkView[]> {
  const { data, error } = await listSourceLinksApiProjectsProjectSourceLinksGet({ path: { project }, throwOnError: false });
  if (error) throw error;
  return data?.source_links ?? [];
}

async function readRepositoryLinks(project: string): Promise<RepositoryLinkView[]> {
  const { data, error } = await listRepositoryLinksApiProjectsProjectRepositoryLinksGet({
    path: { project },
    throwOnError: false,
  });
  if (error) throw error;
  return data?.repository_links ?? [];
}

async function readChunkCounts(slug: string, doneSince: string | null = null): Promise<ChunkCountsView> {
  const query = doneSince === null ? { project: slug } : { project: slug, done_since: doneSince };
  const { data, error } = await chunkCountsApiChunkCountsGet({ query, throwOnError: false });
  if (error) throw error;
  return data!;
}

/** `GET /api/chunk-counts?project=&done_since=` — the project's chunks per status and how many finished since
 * `doneSince()`, the caller's own day boundary, at rest while `slug()` is `null`. Keyed under the header's read for
 * that project's lens, so the live chunk events refresh it with the header's. */
export function injectProjectChunkCountsQuery(slug: () => string | null, doneSince: () => string) {
  return injectQuery(() => {
    const project = slug();
    const since = doneSince();
    return {
      queryKey: [...lensedKey(hubChunkCountsKey, project), 'done-since', since],
      enabled: project !== null,
      queryFn: () => readChunkCounts(project!, since),
    };
  });
}

interface ProjectLinkSets {
  readonly sourceLinks: readonly SourceLinkView[];
  readonly repositoryLinks: readonly RepositoryLinkView[];
}

/** Each listed project's links, by project id — what the projects list judges a row's wiring by. The links sit under
 * `hubProjectsKey`, so a project or link write refreshes them. */
export function injectProjectFigures(projects: () => readonly ProjectView[]): Signal<ReadonlyMap<string, ProjectFigures>> {
  const links = injectQuery(() => {
    const ids = projects().map((project) => project.project_id);
    return {
      queryKey: [...hubProjectsKey, 'figures', ids],
      enabled: ids.length > 0,
      queryFn: (): Promise<ProjectLinkSets[]> =>
        Promise.all(
          ids.map(async (id) => ({
            sourceLinks: await readSourceLinks(id),
            repositoryLinks: await readRepositoryLinks(id),
          })),
        ),
    };
  });
  return computed(() => {
    const linkSets = links.data();
    return new Map(
      projects().map((project, index) => [
        project.project_id,
        {
          sourceLinks: linkSets?.[index]?.sourceLinks,
          repositoryLinks: linkSets?.[index]?.repositoryLinks,
        },
      ]),
    );
  });
}
