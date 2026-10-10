import type {
  GardenProposalView,
  ProjectView,
  RoutineView,
  RunRowView,
  RunnerRegistryView,
  ScopeView,
} from 'fleet';

import { lensLink } from '../../core/project-lens';
import { servingRunners } from '../../core/project-serving';
import { GARDENING_DOORWAY_URL, GARDENING_FIRST_TAB } from '../core/garden-project';
import { isGardenProposalWaiting } from '../proposals/gardening-proposals-page.model';

/** One project's card on the gardening doorway — its garden's standing at a glance, and the way in. */
export interface DoorwayCard {
  readonly projectId: string;
  readonly slug: string;
  readonly name: string;
  /** The way into the project's garden: its first tab, under its lens. */
  readonly link: readonly unknown[];
  /** The names of the project's routines that are not retired, in the order the hub lists them. */
  readonly routines: readonly string[];
  /** How many of the project's scopes are not retired. */
  readonly scopes: number;
  /** How many runs the project minted in the reporting window. */
  readonly runs: number;
  /** How many of the project's proposals wait on a person. */
  readonly waiting: number;
  /** Whether nothing is planted yet: no scope and no routine. */
  readonly unplanted: boolean;
  /** Whether no live runner serves the project, so the work its garden raises waits; `null` until runners are read. */
  readonly unserved: boolean | null;
}

/** The tenant-wide garden reads the doorway groups by project; a read not yet answered is `undefined`. */
export interface DoorwayReads {
  readonly projects: readonly ProjectView[];
  readonly scopes: readonly ScopeView[];
  readonly routines: readonly RoutineView[];
  readonly runs: readonly RunRowView[];
  readonly proposals: readonly GardenProposalView[];
  readonly runners: readonly RunnerRegistryView[] | undefined;
}

function ownedBy<T extends { readonly project_id?: string | null }>(rows: readonly T[], projectId: string): T[] {
  return rows.filter((row) => row.project_id === projectId);
}

/** One card per project of the tenant, by name — each counting only the records whose `project_id` is its own. */
export function doorwayCards(reads: DoorwayReads): readonly DoorwayCard[] {
  return [...reads.projects]
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((project) => {
      const id = project.project_id;
      const routines = ownedBy(reads.routines, id).filter((routine) => routine.retired !== true);
      const scopes = ownedBy(reads.scopes, id).filter((scope) => scope.retired !== true);
      return {
        projectId: id,
        slug: project.slug,
        name: project.name,
        link: lensLink([GARDENING_DOORWAY_URL, GARDENING_FIRST_TAB], project.slug),
        routines: routines.map((routine) => routine.name),
        scopes: scopes.length,
        runs: ownedBy(reads.runs, id).length,
        waiting: ownedBy(reads.proposals, id).filter(isGardenProposalWaiting).length,
        unplanted: routines.length === 0 && scopes.length === 0,
        unserved: reads.runners === undefined ? null : servingRunners(id, reads.runners).length === 0,
      };
    });
}

/** The waiting badge's text: "1 proposal waiting", "3 proposals waiting". */
export function waitingLabel(count: number): string {
  return `${count} proposal${count === 1 ? '' : 's'} waiting`;
}
