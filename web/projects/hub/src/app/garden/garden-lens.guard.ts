import { inject } from '@angular/core';
import { type CanActivateFn, RedirectCommand, Router } from '@angular/router';
import { QueryClient } from '@tanstack/angular-query-experimental';

import type { ProjectView } from 'fleet';

import { ProjectLens, lensSegmentOf, lensedUrl, unlensedUrl } from '../core/project-lens';
import { projectsQueryOptions } from '../core/projects.query';
import { GARDENING_DOORWAY_URL, GARDENING_LANDING_TAB } from './core/garden-project';
import { isRecordTab, readRecordProject } from './core/garden-record.query';

/** The tabs whose record segment is a name, unique only inside its project. */
const NAMED_RECORDS: Readonly<Record<string, string>> = { scopes: 'scope', routines: 'routine' };

/** The tab and the record a gardening URL on All names: `/gardening/{tab}/{record}`, either part `null` when absent. */
export function gardenPathOf(url: string): { readonly tab: string | null; readonly record: string | null } {
  const segments = unlensedUrl(url).split(/[?#]/, 1)[0].split('/').filter((segment) => segment !== '');
  const [, tab, record] = segments;
  return { tab: tab ?? null, record: record === undefined ? null : decodeURIComponent(record) };
}

/** The tenant's projects, once the shell has entered its tenant; a failed read counts as none. */
async function tenantProjects(): Promise<readonly ProjectView[]> {
  const lens = inject(ProjectLens);
  const queryClient = inject(QueryClient);
  await lens.whenReady();
  return queryClient.ensureQueryData(projectsQueryOptions()).catch((): readonly ProjectView[] => []);
}

function redirectTo(router: Router, url: string): RedirectCommand {
  return new RedirectCommand(router.parseUrl(url), { replaceUrl: true });
}

/**
 * The doorway's guard. Under a lens, `/p/{project}/gardening` opens the project's landing tab. On All, a tenant holding
 * exactly one project has no doorway to show: its garden opens instead. With several projects the doorway opens.
 */
export const gardeningDoorwayGuard: CanActivateFn = async (_route, state) => {
  const router = inject(Router);
  const lensed = lensSegmentOf(state.url);
  if (lensed !== null) return redirectTo(router, lensedUrl(`${GARDENING_DOORWAY_URL}/${GARDENING_LANDING_TAB}`, lensed));
  const projects = await tenantProjects();
  if (projects.length === 1) {
    return redirectTo(router, lensedUrl(`${GARDENING_DOORWAY_URL}/${GARDENING_LANDING_TAB}`, projects[0].slug));
  }
  return true;
};

/**
 * A project's garden guard: its tabs exist only under a lens, so a gardening link on All is followed into a project.
 *
 * - With exactly one project, the link opens under its lens as it is.
 * - A run, a finding or a proposal is named by an id unique in the tenant: the link opens under the lens of the
 *   project its own read names. A record the tenant does not hold opens the doorway with a notice.
 * - A scope's slug and a routine's name are unique only inside a project, and the board never matches one across
 *   projects: the link opens the doorway with a notice to choose the project.
 * - A bare tab opens the doorway.
 */
export const gardenProjectGuard: CanActivateFn = async (_route, state) => {
  if (lensSegmentOf(state.url) !== null) return true;
  const router = inject(Router);
  const lens = inject(ProjectLens);
  const projects = await tenantProjects();
  if (projects.length === 1) return redirectTo(router, lensedUrl(state.url, projects[0].slug));

  const { tab, record } = gardenPathOf(state.url);
  const toDoorway = (notice: string | null) => {
    if (notice !== null) lens.notify(notice);
    return redirectTo(router, GARDENING_DOORWAY_URL);
  };
  if (record === null || tab === null) return toDoorway(null);

  const named = NAMED_RECORDS[tab];
  if (named !== undefined) {
    return toDoorway(`A ${named} is named only inside its project, and this tenant holds several: choose the project to open ${named} “${record}” in.`);
  }
  if (!isRecordTab(tab)) return toDoorway(null);
  const project = await readRecordProject(tab, record);
  if (project === null) return toDoorway(`No gardening record “${record}” in this tenant. Choose a project.`);
  return redirectTo(router, lensedUrl(state.url, project));
};
