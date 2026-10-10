import type { ProjectView } from 'fleet/shell';

/** What a tenant-wide `surface` says under the lens on `project`: that the lens narrows nothing there. `null` on All. */
export function lensNoteText(surface: string, project: ProjectView | null): string | null {
  if (project === null) return null;
  return `${surface} belong to the whole tenant: the lens on ${project.name} does not narrow this view.`;
}
