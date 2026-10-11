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

async function readChunkCounts(slug: string): Promise<ChunkCountsView> {
  const { data, error } = await chunkCountsApiChunkCountsGet({ query: { project: slug }, throwOnError: false });
  if (error) throw error;
  return data!;
}

/** `GET /api/chunk-counts?project=` — the project's chunks per status, at rest while `slug()` is `null`. Keyed as the
 * header's read under that project's lens, so the two share one cache entry. */
export function injectProjectChunkCountsQuery(slug: () => string | null) {
  return injectQuery(() => {
    const project = slug();
    return {
      queryKey: lensedKey(hubChunkCountsKey, project),
      enabled: project !== null,
      queryFn: () => readChunkCounts(project!),
    };
  });
}

interface ProjectLinkSets {
  readonly sourceLinks: readonly SourceLinkView[];
  readonly repositoryLinks: readonly RepositoryLinkView[];
}

/** Each listed project's links and chunk counts, by project id — the figures the projects list shows per row. Two
 * reads over the listed projects, one per freshness: the links sit under `hubProjectsKey`, so a project or link write
 * refreshes them, and the counts under `hubChunkCountsKey`, so the live chunk events do. */
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
  const counts = injectQuery(() => {
    const slugs = projects().map((project) => project.slug);
    return {
      queryKey: [...hubChunkCountsKey, 'per-project', slugs],
      enabled: slugs.length > 0,
      queryFn: (): Promise<ChunkCountsView[]> => Promise.all(slugs.map(readChunkCounts)),
    };
  });
  return computed(() => {
    const linkSets = links.data();
    const chunkCounts = counts.data();
    return new Map(
      projects().map((project, index) => [
        project.project_id,
        {
          sourceLinks: linkSets?.[index]?.sourceLinks,
          repositoryLinks: linkSets?.[index]?.repositoryLinks,
          counts: chunkCounts?.[index],
        },
      ]),
    );
  });
}
