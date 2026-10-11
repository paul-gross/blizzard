import { type Signal, computed, inject } from '@angular/core';

import type { ProjectView } from 'fleet/shell';

import { ProjectLens } from './project-lens';
import { injectProjectsQuery } from './projects.query';

/** The project of `projects` the lens names by `slug`, `null` on All or while the list does not hold it. */
export function lensedProject(projects: readonly ProjectView[] | undefined, slug: string | null): ProjectView | null {
  if (slug === null) return null;
  return projects?.find((project) => project.slug === slug) ?? null;
}

/**
 * The project the lens is on, from the tenant's projects read — `null` on All, and until that read holds it. The lens
 * guard settles the URL on the project's current slug, so the slug finds it.
 */
export function injectLensedProject(): Signal<ProjectView | null> {
  const lens = inject(ProjectLens);
  const projects = injectProjectsQuery(() => lens.lensed());
  return computed(() => lensedProject(projects.data(), lens.slug()));
}
