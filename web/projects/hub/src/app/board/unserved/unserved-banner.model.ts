import type { ProjectView, RunnerRegistryView } from 'fleet';

import { servingRunners } from '../../core/project-serving';

/**
 * The banner the board shows under a project lens when no live runner serves the project — its waiting work reads as
 * unserved rather than slow. `null` (no banner) on All, while the lensed project or the runners are not yet read, and
 * whenever a live runner serves the project.
 */
export function unservedBannerText(
  project: ProjectView | null,
  runners: readonly RunnerRegistryView[] | undefined,
): string | null {
  if (project === null || runners === undefined) return null;
  if (servingRunners(project.project_id, runners).length > 0) return null;
  return `No live runner serves ${project.name}: its ready work waits in the queue until one does.`;
}
