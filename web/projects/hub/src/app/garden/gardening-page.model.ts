import { type GardenProposalView, type ProjectView, type RunnerRegistryView } from 'fleet';

import { servingRunners } from '../core/project-serving';
import { isGardenProposalWaiting } from './proposals/gardening-proposals-page.model';

/** How many proposals are waiting on a person — the gardening strip's one urgent count. */
export function waitingProposalCount(proposals: readonly GardenProposalView[]): number {
  return proposals.filter(isGardenProposalWaiting).length;
}

/** The gardening tabs' labels, by their route segment — the breadcrumb's surface crumb. */
const SURFACE_LABELS: Readonly<Record<string, string>> = {
  runs: 'Runs',
  findings: 'Findings',
  proposals: 'Proposals',
  routines: 'Routines',
  scopes: 'Scopes',
};

/** The surface crumb for the gardening tab `tab` names, `null` when it names none. */
export function surfaceLabel(tab: string | null): string | null {
  return tab === null ? null : (SURFACE_LABELS[tab] ?? null);
}

/** The project crumb's choices: the tenant's projects by name, then All — the doorway. */
export interface GardenProjectChoice {
  readonly slug: string | null;
  readonly label: string;
}

export function gardenProjectChoices(projects: readonly ProjectView[]): readonly GardenProjectChoice[] {
  return [
    ...[...projects]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((project) => ({ slug: project.slug, label: project.name })),
    { slug: null, label: 'All projects' },
  ];
}

/**
 * Whether no live runner serves `project`, so the work its garden raises waits — `false` until both the project and
 * the runners are read, so the warning never flashes on a read still in flight.
 */
export function gardenUnserved(
  project: ProjectView | null,
  runners: readonly RunnerRegistryView[] | undefined,
): boolean {
  if (project === null || runners === undefined) return false;
  return servingRunners(project.project_id, runners).length === 0;
}
