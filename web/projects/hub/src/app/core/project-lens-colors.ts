import { type Signal, computed, inject } from '@angular/core';

import type { ChunkDetail } from 'fleet';

import { ProjectLens } from './project-lens';
import { injectProjectsQuery } from './projects.query';

/** Whether project colouring carries information: the lens is on All and the tenant has more than one project. */
export function showsProjectColors(slug: string | null, projectCount: number): boolean {
  return slug === null && projectCount > 1;
}

/**
 * Whether the board paints project colours — swatches, stripes, tints. Under a one-project lens, or on a
 * one-project tenant, every chunk is the same project, so the colour says nothing and is dropped.
 */
export function injectShowProjectColors(): Signal<boolean> {
  const lens = inject(ProjectLens);
  const projects = injectProjectsQuery();
  return computed(() => showsProjectColors(lens.slug(), projects.data()?.length ?? 0));
}

/** The open chunk's project's color, joined from the projects list by `project_id` — `null` where project colours
 * are not shown ({@link injectShowProjectColors}), while the dock is closed, before the list is read, or when the
 * project is unknown. */
export function openProjectColor(
  shown: boolean,
  detail: ChunkDetail | undefined,
  projects: readonly { readonly project_id: string; readonly color: string }[] | undefined,
): string | null {
  const projectId = detail?.project_id;
  if (!shown || !projectId) return null;
  return projects?.find((project) => project.project_id === projectId)?.color ?? null;
}
