import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { compactRef } from '../compact-ref';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitPanel } from '../kit/kit-panel';
import { KitSelectRow } from '../kit/kit-select-row';
import { FleetWhen } from '../when-display';

/** One row of the proposal docket list — just enough to pick a proposal (`hub
 * garden-proposal list` is the read this list serves). `waiting` is the row's own
 * copy of `isGardenProposalWaiting`, so the list never re-derives it from a closure
 * it doesn't carry. */
export interface ProposalListRowVm {
  readonly proposalId: string;
  readonly title: string;
  readonly proposalClass: string;
  readonly waiting: boolean;
  readonly createdAt: string;
}

/**
 * The garden proposal docket's list — presentational only, no query injection.
 * Renders the rows it is handed (already filtered by the container's waiting/class
 * state), highlights `selectedId`, and emits `proposalPick` on a row click; the
 * container owns what "selected" then does. `FleetRoutineList`'s own shape.
 */
@Component({
  selector: 'fleet-proposal-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitAsyncState, KitPanel, KitSelectRow],
  templateUrl: './proposal-list.html',
  styleUrl: './proposal-list.css',
})
export class FleetProposalList {
  readonly rows = input.required<readonly ProposalListRowVm[]>();
  readonly selectedId = input<string | null>(null);
  readonly state = input.required<KitAsyncStateValue>();

  /** Named `proposalPick`, not `select` — `@angular-eslint/no-output-native` forbids
   * an output shadowing the native DOM `select` event. */
  readonly proposalPick = output<string>();

  protected readonly compactRef = compactRef;

  protected pick(proposalId: string): void {
    this.proposalPick.emit(proposalId);
  }
}
