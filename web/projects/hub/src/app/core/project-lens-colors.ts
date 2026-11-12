import { type Signal, computed, inject } from '@angular/core';

import type { ChunkDetail } from 'fleet';

import { ProjectLens } from './project-lens';
import { injectProjectsQuery } from './projects.query';

/** Whether project colouring carries information: the lens is on All and the tenant has more than one project. */
export function showsProjectColors(
  slug: string | null,
  projectCount: number,
): boolean {
  return slug === null && projectCount > 1;
}

/**
 * Whether the board paints project colours — swatches, stripes, tints. Under a one-project lens, or on a
 * one-project tenant, every chunk is the same project, so the colour says nothing and is dropped.
 */
export function injectShowProjectColors(): Signal<boolean> {
  const lens = inject(ProjectLens);
  const projects = injectProjectsQuery();
  return computed(() =>
    showsProjectColors(lens.slug(), projects.data()?.length ?? 0),
  );
}

export function projectColorOf(
  shown: boolean,
  projects: readonly { readonly project_id: string; readonly color: string }[] | undefined,
  projectId: string | null | undefined,
): string | null {
  if (!shown || !projectId) return null;
  return projects?.find((project) => project.project_id === projectId)?.color ?? null;
}

/**
 * Each project's colour by id, or `null` where project colours are not shown ({@link injectShowProjectColors}) — the
 * gate lives here so no caller can forget it. A consumer reads `null` as "wear another colour" (the runner's, on
 * Tracks) or "no colour".
 */
export function projectColorMap(
  shown: boolean,
  projects: readonly { readonly project_id: string; readonly color: string }[] | undefined,
): ReadonlyMap<string, string> | null {
  if (!shown) return null;
  return new Map((projects ?? []).map((project) => [project.project_id, project.color]));
}

/** The open chunk's project's color, joined from the projects list by `project_id` — `null` where project colours
 * are not shown ({@link injectShowProjectColors}), while the dock is closed, before the list is read, or when the
 * project is unknown. */
export function openProjectColor(
  shown: boolean,
  detail: ChunkDetail | undefined,
  projects:
    | readonly { readonly project_id: string; readonly color: string }[]
    | undefined,
): string | null {
  return projectColorOf(shown, projects, detail?.project_id);
}
