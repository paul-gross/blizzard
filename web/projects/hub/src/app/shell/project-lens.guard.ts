import { inject } from '@angular/core';
import { type CanActivateFn, RedirectCommand, Router } from '@angular/router';
import { QueryClient } from '@tanstack/angular-query-experimental';

import { type ProjectView, errorMessage, hubProjectsKey } from 'fleet/shell';

import { ProjectLens, lensSegmentOf, lensedUrl, unlensedUrl } from '../core/project-lens';
import { projectsQueryOptions, readProject } from '../core/projects.query';

/**
 * Settles a lensed URL's project segment, in the tenant the shell has entered (it waits for that first):
 *
 * - a current slug of the tenant's projects opens as it is;
 * - a project's id, or a slug a project has left, is replaced in the URL by the project's current slug;
 * - a segment naming no project of the tenant opens the same view on All, with a notice.
 *
 * The segment is matched against the tenant's project list first; only one the list does not hold — possibly a former
 * slug — is asked of the hub by itself. That read's `404` means only that the tenant has no such project: the board
 * stays in its tenant and the page does not reload.
 */
export const projectLensGuard: CanActivateFn = async (_route, state) => {
  const lens = inject(ProjectLens);
  const router = inject(Router);
  const queryClient = inject(QueryClient);
  const segment = lensSegmentOf(state.url) ?? '';

  await lens.whenReady();

  const listed = await queryClient.ensureQueryData(projectsQueryOptions()).catch((): readonly ProjectView[] => []);
  if (listed.some((project) => project.slug === segment)) return true;

  const redirectTo = (url: string) => new RedirectCommand(router.parseUrl(url), { replaceUrl: true });
  const underSlug = (slug: string) => redirectTo(lensedUrl(unlensedUrl(state.url), slug));

  const byId = listed.find((project) => project.project_id === segment);
  if (byId !== undefined) return underSlug(byId.slug);

  let named: ProjectView | null;
  try {
    named = await readProject(segment);
  } catch (error) {
    lens.notify(`Could not open project “${segment}” (${errorMessage(error, 'the hub did not answer')}). Showing all projects.`);
    return redirectTo(unlensedUrl(state.url));
  }
  if (named === null) {
    lens.notify(`No project “${segment}” in this tenant. Showing all projects.`);
    return redirectTo(unlensedUrl(state.url));
  }
  // A project the list read before it was created: its slug is current, and the list is behind.
  if (named.slug === segment) {
    void queryClient.invalidateQueries({ queryKey: hubProjectsKey });
    return true;
  }
  return underSlug(named.slug);
};
