import { ProjectColor } from 'fleet/hub-api';

/** Named palette offered by the hub, in display order. Kept apart from `project.translate.ts`, which the eager shell
 * reaches, so the generated vocabulary stays off the initial chunk. */
export const PROJECT_COLORS: readonly string[] = Object.values(ProjectColor);
