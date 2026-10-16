import type { GardenProposalView, ProjectView, RoutineView, RunRowView, ScopeView } from 'fleet';

import { lensLink } from '../../core/project-lens';
import { GARDENING_DOORWAY_URL, GARDENING_LANDING_TAB } from '../core/garden-project';
import type { TenantResolution } from '../../core/auth/tenant-context';
import { isGardenProposalWaiting, waitingLabel } from '../proposals/gardening-proposals-page.model';

export { waitingLabel };

/** The outcomes of a run still sweeping: the card counts these as in progress. */
const IN_PROGRESS: ReadonlySet<string> = new Set(['running', 'delivering']);

/** One project's card on the gardening doorway — its garden's standing at a glance, and the way in. */
export interface DoorwayCard {
  readonly projectId: string;
  readonly slug: string;
  readonly name: string;
  /** The way into the project's garden: its landing tab, under its lens. */
  readonly link: readonly unknown[];
  /** The names of the project's routines that are not retired, in the order the hub lists them. */
  readonly routines: readonly string[];
  /** How many of the project's scopes are not retired. */
  readonly scopes: number;
  /** How many of the project's runs in the reporting window are still sweeping. */
  readonly inProgress: number;
  /** How many findings the project's runs in the reporting window added. */
  readonly findings: number;
  /** When the project's newest finished run in the reporting window was minted; `null` when none finished. */
  readonly lastRun: string | null;
  /** How many of the project's proposals wait on a person. */
  readonly waiting: number;
  /** Whether nothing is planted yet: no scope and no routine. */
  readonly unplanted: boolean;
}

/** The tenant-wide garden reads the doorway groups by project; a read not yet answered is `undefined`. */
export interface DoorwayReads {
  readonly projects: readonly ProjectView[];
  readonly scopes: readonly ScopeView[];
  readonly routines: readonly RoutineView[];
  readonly runs: readonly RunRowView[];
  readonly proposals: readonly GardenProposalView[];
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
      const runs = ownedBy(reads.runs, id);
      const finished = runs.filter((run) => run.outcome === 'done');
      return {
        projectId: id,
        slug: project.slug,
        name: project.name,
        link: lensLink([GARDENING_DOORWAY_URL, GARDENING_LANDING_TAB], project.slug),
        routines: routines.map((routine) => routine.name),
        scopes: scopes.length,
        inProgress: runs.filter((run) => IN_PROGRESS.has(run.outcome)).length,
        findings: runs.reduce((sum, run) => sum + run.delivered.reduce((added, set) => added + set.added_count, 0), 0),
        lastRun: newest(finished.map((run) => run.minted_at)),
        waiting: ownedBy(reads.proposals, id).filter(isGardenProposalWaiting).length,
        unplanted: routines.length === 0 && scopes.length === 0,
      };
    });
}

function newest(instants: readonly string[]): string | null {
  return instants.reduce<string | null>((seen, at) => (seen === null || Date.parse(at) > Date.parse(seen) ? at : seen), null);
}

/** The scopes row's right-hand state: "idle", or how many runs are sweeping. */
export function sweepingLabel(inProgress: number): string {
  return inProgress === 0 ? 'idle' : `${inProgress} run${inProgress === 1 ? '' : 's'} in progress`;
}

/** The way-in's text, its possessive following the name: "Open Default's garden", "Open Widgets' garden". */
export function openGardenLabel(name: string): string {
  return `Open ${name}${/s$/i.test(name) ? "'" : "'s"} garden`;
}

/** The tenant a garden doorway shows, from the identity's memberships settled by `resolve`; empty until settled. */
export function doorwayTenantName(resolution: TenantResolution | null): string {
  return resolution?.kind === 'selected' ? resolution.tenant.name : '';
}

/** The doorway panel's title: "Gardens in {tenant}", or "Gardens" while the tenant is not yet named. */
export function doorwayTitle(tenantName: string): string {
  return tenantName === '' ? 'Gardens' : `Gardens in ${tenantName}`;
}
