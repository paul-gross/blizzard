import { type GardenProposalView, type ProjectView, type RunnerRegistryView } from 'fleet';

import { servingRunners } from '../core/project-serving';
import { isGardenProposalWaiting, waitingLabel } from './proposals/gardening-proposals-page.model';

export { waitingLabel };

/** How many proposals are waiting on a person — the gardening strip's one urgent count. */
export function waitingProposalCount(proposals: readonly GardenProposalView[]): number {
  return proposals.filter(isGardenProposalWaiting).length;
}

/**
 * The surfaces of a project's garden, in the strip's order. Runs and findings share one surface: a run is a sweep and
 * its findings are what it found, so the findings list is that surface's second view.
 */
export const GARDEN_SURFACES = ['scopes', 'routines', 'runs', 'proposals'] as const;
export type GardenSurface = (typeof GARDEN_SURFACES)[number];

/** The route segments each surface covers. */
const SURFACE_TABS: Readonly<Record<string, GardenSurface>> = {
  scopes: 'scopes',
  routines: 'routines',
  runs: 'runs',
  findings: 'runs',
  proposals: 'proposals',
};

const SURFACE_LABELS: Readonly<Record<GardenSurface, string>> = {
  scopes: 'Scopes',
  routines: 'Routines',
  runs: 'Runs & findings',
  proposals: 'Proposals',
};

/** The surface the gardening tab `tab` belongs to, `null` when it names none. */
export function surfaceOf(tab: string | null): GardenSurface | null {
  return tab === null ? null : (SURFACE_TABS[tab] ?? null);
}

/** The surface crumb for the gardening tab `tab` names, `null` when it names none. */
export function surfaceLabel(tab: string | null): string | null {
  const surface = surfaceOf(tab);
  return surface === null ? null : SURFACE_LABELS[surface];
}

/** One tab of the strip: its route segment, label and record count (`null` until its read answers). */
export interface GardenSurfaceTab {
  readonly surface: GardenSurface;
  readonly label: string;
  readonly count: number | null;
}

/**
 * The tabs' counts from the lists' reads, each `null` until its read answers: the scopes, routines and runs listed,
 * and the proposals waiting on a person.
 */
export function gardenSurfaceCounts(reads: {
  readonly scopes: readonly unknown[] | undefined;
  readonly routines: readonly unknown[] | undefined;
  readonly runs: readonly unknown[] | undefined;
  readonly proposals: readonly GardenProposalView[] | undefined;
}): Readonly<Record<GardenSurface, number | null>> {
  return {
    scopes: reads.scopes?.length ?? null,
    routines: reads.routines?.length ?? null,
    runs: reads.runs?.length ?? null,
    proposals: reads.proposals === undefined ? null : waitingProposalCount(reads.proposals),
  };
}

/** The strip's four tabs with their counts: the lists' own lengths, and for Proposals the ones waiting on a person. */
export function gardenSurfaceTabs(counts: Readonly<Record<GardenSurface, number | null>>): readonly GardenSurfaceTab[] {
  return GARDEN_SURFACES.map((surface) => ({ surface, label: SURFACE_LABELS[surface], count: counts[surface] }));
}

/** The project crumb's choices: the tenant's projects by name, each with its proposals waiting, then All — the doorway. */
export interface GardenProjectChoice {
  readonly slug: string | null;
  readonly label: string;
  /** How many of the project's proposals wait on a person; `null` for All, or until the proposals are read. */
  readonly waiting: number | null;
}

export function gardenProjectChoices(
  projects: readonly ProjectView[],
  proposals: readonly GardenProposalView[] | undefined,
): readonly GardenProjectChoice[] {
  return [
    ...[...projects]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((project) => ({
        slug: project.slug,
        label: project.name,
        waiting:
          proposals === undefined
            ? null
            : waitingProposalCount(proposals.filter((proposal) => proposal.project_id === project.project_id)),
      })),
    { slug: null, label: 'All projects', waiting: null },
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
