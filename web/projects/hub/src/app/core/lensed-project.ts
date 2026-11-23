import { type Signal, computed, inject } from '@angular/core';

import { ProjectLens } from './project-lens';
import type { Project } from './project.domain';
import { injectProjectDomainsQuery } from './projects.query';

/** The project of `projects` the lens names by `slug`, `null` on All or while the list does not hold it. */
export function lensedProject(
  projects: readonly Project[] | undefined,
  slug: string | null,
): Project | null {
  if (slug === null) return null;
  return projects?.find((project) => project.slug === slug) ?? null;
}

/**
 * The project the lens is on, from the tenant's projects read — `null` on All, and until that read holds it. The lens
 * guard settles the URL on the project's current slug, so the slug finds it.
 */
export function injectLensedProject(): Signal<Project | null> {
  const lens = inject(ProjectLens);
  const projects = injectProjectDomainsQuery(() => lens.lensed());
  return computed(() => lensedProject(projects.data(), lens.slug()));
}
