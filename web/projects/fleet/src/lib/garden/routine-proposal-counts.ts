import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitPanel } from '../kit/kit-panel';

/** One class's garden-proposal counts over the panel's window (blizzard#547) —
 * `GardenProposalCountsRowView`'s own five counts, already scoped to the one
 * selected routine by the query's own `routine` filter, so no `routineName` rides
 * the row (`RoutinePanelVm.lastSwept`'s own reason for not repeating what every row
 * already shares). `proposalClass` renames the wire's `class`, `FindingListRowVm`'s
 * own `findingClass` rename, so a template never confuses it with the DOM `class`
 * attribute. */
export interface ProposalCountsRowVm {
  readonly proposalClass: string;
  readonly created: number;
  readonly open: number;
  readonly passed: number;
  readonly acceptedWithItem: number;
  readonly acceptedWithoutItem: number;
}

/**
 * The gardening routine detail's garden-proposal counts table (blizzard#547) — how
 * many proposals a routine raised, per class, and how each was closed over the
 * panel's own window. Presentational only, no query injection
 * (`bzh:frontend-container-presentational`): it renders exactly the rows it is
 * handed, wrapped in its own `fleet-kit-panel`/`fleet-kit-async-state` pair so its
 * loading/error/empty states resolve independently of whichever other panel already
 * rendered `'ready'` beside it (`bzh:frontend-empty-state-gated`).
 *
 * A plain `<table>`, `routine-panel.html`'s own last-swept table — `fleet-kit-fact-list`
 * is a single-row label/value grid, not a fit for a multi-row numeric table like this
 * one.
 */
@Component({
  selector: 'fleet-routine-proposal-counts',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitPanel],
  templateUrl: './routine-proposal-counts.html',
  styleUrl: './routine-proposal-counts.css',
})
export class FleetRoutineProposalCounts {
  readonly rows = input<readonly ProposalCountsRowVm[]>([]);
  readonly state = input.required<KitAsyncStateValue>();
}
